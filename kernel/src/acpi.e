// L1 — ACPI mínimo: RSDP, XSDT/RSDT y las tablas que el kernel necesita para dejar el PIC y
// el PIT: MADT (LAPIC, CPUs, IOAPIC, redirecciones de IRQ), MCFG (ECAM de PCIe) y HPET.
// Sólo lectura, con las sumas de comprobación verificadas; no ejecuta AML ni cambia nada.
// Fuente: ACPI Specification 6.5 (UEFI Forum), §5.2.5 (RSDP), §5.2.7–5.2.8 (RSDT/XSDT),
// §5.2.12 (MADT); PCI Firmware Spec 3.x (MCFG); IA-PC HPET Specification 1.0a.
import boot;
import vmm;
import serial;

// RSDP (36 bytes en ACPI 2.0+; 20 en 1.0)
struct AcpiRsdp {
    signature: [8]u8; checksum: u8; oemid: [6]u8; revision: u8; rsdt: u32;
    length: u32; xsdt: u64; ext_checksum: u8; reserved: [3]u8;
}
// Cabecera común de toda tabla de descripción del sistema (36 bytes)
struct AcpiHeader {
    signature: [4]u8; length: u32; revision: u8; checksum: u8; oemid: [6]u8;
    oem_table: [8]u8; oem_revision: u32; creator: u32; creator_revision: u32;
}

const MAX_CPUS: u64 = 64;
const MAX_IOAPICS: u64 = 4;
const MAX_OVERRIDES: u64 = 24;

var acpi_ok: bool = false;
var rsdp_revision: u64 = 0;
var tables: u64 = 0;
var lapic_addr: u64 = 0xFEE00000;
var cpu_count: u64 = 0;
var cpu_apic_ids: [64]u64;
var cpu_flags: [64]u64;
var ioapic_count: u64 = 0;
var ioapic_ids: [4]u64;
var ioapic_addrs: [4]u64;
var ioapic_gsi_bases: [4]u64;
var override_count: u64 = 0;
var override_irqs: [24]u64;
var override_gsis: [24]u64;
var override_flags: [24]u64;
var mcfg_base: u64 = 0;
var mcfg_segment: u64 = 0;
var mcfg_bus_start: u64 = 0;
var mcfg_bus_end: u64 = 0;
var hpet_addr: u64 = 0;
var pcat_compat: bool = false;
var fadt_known: bool = false;
var fadt_boot_arch: u64 = 0;
var fadt_sci: u64 = 0;

fn acpi_present() -> bool { return acpi_ok; }
fn acpi_lapic_addr() -> u64 { return lapic_addr; }
fn acpi_cpu_count() -> u64 { return cpu_count; }
fn acpi_cpu_apic_id(i: u64) -> u64 { if i < cpu_count { return cpu_apic_ids[i]; } return 0; }
fn acpi_ioapic_count() -> u64 { return ioapic_count; }
fn acpi_ioapic_addr(i: u64) -> u64 { if i < ioapic_count { return ioapic_addrs[i]; } return 0; }
fn acpi_ioapic_gsi_base(i: u64) -> u64 { if i < ioapic_count { return ioapic_gsi_bases[i]; } return 0; }
fn acpi_mcfg_base() -> u64 { return mcfg_base; }
fn acpi_hpet_addr() -> u64 { return hpet_addr; }
fn acpi_table_count() -> u64 { return tables; }
// IA-PC boot architecture de la FADT (ACPI 2.0+, offset 109): bit 1 = hay 8042. Sin FADT
// moderna se asume que sí, como hace Linux (x86_platform.legacy.i8042).
fn acpi_has_8042() -> bool { if !fadt_known { return true; } return (fadt_boot_arch & 2) != 0; }
fn acpi_sci_irq() -> u64 { return fadt_sci; }

// Banderas (polaridad bits 0-1, disparo bits 2-3) de la redirección de una IRQ ISA; 0 = por defecto.
fn acpi_irq_flags(irq: u64) -> u64 {
    for i in 0..override_count { if override_irqs[i] == irq { return override_flags[i]; } }
    return 0;
}

// GSI al que va una IRQ ISA, según las redirecciones de la MADT (identidad si no hay).
fn acpi_irq_to_gsi(irq: u64) -> u64 {
    for i in 0..override_count { if override_irqs[i] == irq { return override_gsis[i]; } }
    return irq;
}

fn rd8(a: u64) -> u8 { unsafe { let p = a as ptr<u8>; return p[0]; } }
fn rd16(a: u64) -> u64 { return (rd8(a) as u64) | ((rd8(a + 1) as u64) << 8); }
fn rd32(a: u64) -> u64 { return rd16(a) | (rd16(a + 2) << 16); }
fn rd64(a: u64) -> u64 { return rd32(a) | (rd32(a + 4) << 32); }

fn checksum_ok(a: u64, n: u64) -> bool {
    var sum: u8 = 0;
    for i in 0..n { sum = sum + rd8(a + i); }
    return sum == 0;
}

fn sig_is(a: u64, s: str) -> bool {
    unsafe { for i in 0..4 { if rd8(a + i) != s[i] { return false; } } }
    return true;
}

fn virt(phys: u64) -> u64 { return phys + boot_hhdm(); }

// Las tablas viven en memoria acpi-reclaim/acpi-nvs, que ni el HHDM de Limine (revisión 3)
// ni vmm_init mapean: se mapean aquí, en el HHDM del kernel, antes de leer nada.
fn map_acpi_regions() {
    for i in 0..boot_acpi_region_count() {
        let base = boot_acpi_region_base(i);
        let len = boot_acpi_region_len(i);
        let first = base & 0xFFFFFFFFFFFFF000;
        let pages = (base + len - first + 4095) / 4096;
        vmm_map_hhdm(first, pages);
    }
}

fn parse_madt(t: u64, len: u64) {
    lapic_addr = rd32(t + 36);
    pcat_compat = (rd32(t + 40) & 1) != 0;
    var p = t + 44;
    while p + 2 <= t + len {
        let kind = rd8(p) as u64;
        let n = rd8(p + 1) as u64;
        if n < 2 { break; }
        if kind == 0 && n >= 8 {
            if cpu_count < MAX_CPUS {
                cpu_apic_ids[cpu_count] = rd8(p + 3) as u64;
                cpu_flags[cpu_count] = rd32(p + 4);
                cpu_count += 1;
            }
        } else if kind == 1 && n >= 12 {
            if ioapic_count < MAX_IOAPICS {
                ioapic_ids[ioapic_count] = rd8(p + 2) as u64;
                ioapic_addrs[ioapic_count] = rd32(p + 4);
                ioapic_gsi_bases[ioapic_count] = rd32(p + 8);
                ioapic_count += 1;
            }
        } else if kind == 2 && n >= 10 {
            if override_count < MAX_OVERRIDES {
                override_irqs[override_count] = rd8(p + 3) as u64;
                override_gsis[override_count] = rd32(p + 4);
                override_flags[override_count] = rd16(p + 8);
                override_count += 1;
            }
        } else if kind == 5 && n >= 12 {
            lapic_addr = rd64(p + 4);          // dirección del LAPIC de 64 bits
        } else if kind == 9 && n >= 16 {
            // x2APIC local: ids de 32 bits (sistemas con más de 255 CPUs)
            if cpu_count < MAX_CPUS {
                cpu_apic_ids[cpu_count] = rd32(p + 4);
                cpu_flags[cpu_count] = rd32(p + 8);
                cpu_count += 1;
            }
        }
        p += n;
    }
}

fn parse_mcfg(t: u64, len: u64) {
    if len < 60 { return; }
    mcfg_base = rd64(t + 44);
    mcfg_segment = rd16(t + 52);
    mcfg_bus_start = rd8(t + 54) as u64;
    mcfg_bus_end = rd8(t + 55) as u64;
}

fn visit_table(phys: u64) {
    let t = virt(phys);
    let len = rd32(t + 4);
    if len < 36 || len > 0x100000 { kprintf("[acpi] tabla en %x con longitud invalida %d\n", phys, len); return; }
    let ok = checksum_ok(t, len);
    var sig: [5]u8;
    for i in 0..4 { sig[i] = rd8(t + i); }
    sig[4] = 0;
    unsafe { kprintf("[acpi] tabla %s en %x bytes=%d rev=%d suma_ok=%d\n", &sig[0] as str, phys, len, rd8(t + 8) as u64, ok as u64); }
    tables += 1;
    if !ok { return; }
    if sig_is(t, "APIC") { parse_madt(t, len); }
    else if sig_is(t, "MCFG") { parse_mcfg(t, len); }
    else if sig_is(t, "HPET") { if len >= 56 { hpet_addr = rd64(t + 44); } }
    else if sig_is(t, "FACP") {
        if len >= 48 { fadt_sci = rd16(t + 46); }
        if len >= 111 && (rd8(t + 8) as u64) >= 2 { fadt_boot_arch = rd16(t + 109); fadt_known = true; }
    }
}

// Lee el RSDP que entregó Limine y recorre la XSDT (o la RSDT). Devuelve false, sin tocar
// nada, si no hay ACPI o las sumas no cuadran.
fn acpi_init() -> bool {
    acpi_ok = false; tables = 0; cpu_count = 0; ioapic_count = 0; override_count = 0;
    map_acpi_regions();
    let rsdp = boot_rsdp();
    if rsdp == 0 { kprintf("[acpi] el cargador no entrego RSDP\n"); return false; }
    unsafe {
        let r = rsdp as ptr<AcpiRsdp>;
        rsdp_revision = r.revision as u64;
        if !checksum_ok(rsdp, 20) { kprintf("[acpi] RSDP con suma invalida\n"); return false; }
        var root: u64 = 0;
        var wide = false;
        if r.revision >= 2 && r.xsdt != 0 && checksum_ok(rsdp, 36) { root = r.xsdt; wide = true; }
        else { root = r.rsdt as u64; }
        kprintf("[acpi] rsdp rev=%d %s=%x oem=%c%c%c%c%c%c\n", rsdp_revision, "xsdt", root,
                r.oemid[0] as u64, r.oemid[1] as u64, r.oemid[2] as u64, r.oemid[3] as u64, r.oemid[4] as u64, r.oemid[5] as u64);
        let rt = virt(root);
        let rlen = rd32(rt + 4);
        if rlen < 36 || !checksum_ok(rt, rlen) { kprintf("[acpi] tabla raiz invalida\n"); return false; }
        var entry_size: u64 = 4;
        if wide { entry_size = 8; }
        let n = (rlen - 36) / entry_size;
        for i in 0..n {
            var phys: u64 = 0;
            if wide { phys = rd64(rt + 36 + i * 8); } else { phys = rd32(rt + 36 + i * 4); }
            if phys != 0 { visit_table(phys); }
        }
    }
    acpi_ok = true;
    kprintf("[acpi] madt lapic=%x cpus=%d ioapics=%d redirecciones=%d pcat=%d\n", lapic_addr, cpu_count, ioapic_count, override_count, pcat_compat as u64);
    for i in 0..cpu_count { kprintf("[acpi] cpu %d apic_id=%d flags=%x\n", i, cpu_apic_ids[i], cpu_flags[i]); }
    for i in 0..ioapic_count { kprintf("[acpi] ioapic id=%d addr=%x gsi_base=%d\n", ioapic_ids[i], ioapic_addrs[i], ioapic_gsi_bases[i]); }
    for i in 0..override_count { kprintf("[acpi] irq %d -> gsi %d flags=%x\n", override_irqs[i], override_gsis[i], override_flags[i]); }
    if mcfg_base != 0 { kprintf("[acpi] mcfg ecam=%x segmento=%d buses=%d..%d\n", mcfg_base, mcfg_segment, mcfg_bus_start, mcfg_bus_end); }
    if hpet_addr != 0 { kprintf("[acpi] hpet=%x\n", hpet_addr); }
    if fadt_known { kprintf("[acpi] fadt boot_arch=%x (8042=%d) sci=%d\n", fadt_boot_arch, acpi_has_8042() as u64, fadt_sci); }
    return true;
}
