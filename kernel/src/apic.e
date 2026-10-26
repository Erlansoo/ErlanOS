// L1 — APIC local (LAPIC), su temporizador, el IOAPIC con las rutas de las IRQ ISA, y las
// direcciones y datos de los mensajes MSI. Con esto el reloj del sistema deja el PIT (IRQ0 por
// el 8259) y pasa al temporizador del LAPIC, teclado y ratón (IRQ1, IRQ12) llegan por el
// IOAPIC con el 8259 apagado, y los dispositivos PCI pueden interrumpir por MSI.
// Fuente: Intel SDM vol. 3A cap. 11 (Advanced Programmable Interrupt Controller), 82093AA
// I/O APIC datasheet, PCI Local Bus 3.0 §6.8 (MSI). De Linux arch/x86/kernel/apic/io_apic.c
// (GPL-2.0, sólo leído) se tomó el orden de escritura de las entradas, la decodificación de las
// banderas de la MADT y el enmascarado de LINT0 al dejar el modo PIC; ver docs/APIC.md.
import acpi;
import cpu;
import mmio;
import vmm;
import pic;
import serial;

// Registros del LAPIC (offsets en su base, cada uno de 32 bits en 16 bytes)
const LAPIC_ID: u64 = 0x020;
const LAPIC_VERSION: u64 = 0x030;
const LAPIC_TPR: u64 = 0x080;
const LAPIC_EOI: u64 = 0x0B0;
const LAPIC_SVR: u64 = 0x0F0;
const LAPIC_ESR: u64 = 0x280;
const LAPIC_LVT_TIMER: u64 = 0x320;
const LAPIC_LVT_LINT0: u64 = 0x350;
const LAPIC_LVT_ERROR: u64 = 0x370;
const LAPIC_TIMER_INIT: u64 = 0x380;
const LAPIC_TIMER_CUR: u64 = 0x390;
const LAPIC_TIMER_DIV: u64 = 0x3E0;
const SVR_ENABLE: u32 = 1 << 8;
const LVT_MASK: u32 = 1 << 16;
const LVT_PERIODIC: u32 = 1 << 17;
const IA32_APIC_BASE: u32 = 0x1B;

// Vectores que usa ErlanOS por encima de los 16 del PIC (32..47).
const VECTOR_TIMER: u64 = 48;
const VECTOR_SPURIOUS: u64 = 255;

const KMMIO_LAPIC: u64 = 0xFFFFFFFFA0200000;
const KMMIO_IOAPIC: u64 = 0xFFFFFFFFA0201000;

var lapic: u64 = 0;
var apic_ok: bool = false;
var apic_id: u64 = 0;
var timer_per_10ms: u64 = 0;
var timer_hz: u64 = 0;
var timer_ticks: u64 = 0;
var ioapic: u64 = 0;
var ioapic_entries: u64 = 0;
var ioapic_mode: bool = false;
var ioapic_version: u64 = 0;

fn lrd(off: u64) -> u32 { return mmio_read32(lapic + off); }
fn lwr(off: u64, v: u32) { mmio_write32(lapic + off, v); }

fn apic_present() -> bool { return apic_ok; }
fn apic_cpu_id() -> u64 { return apic_id; }
fn apic_timer_hz() -> u64 { return timer_hz; }
fn apic_timer_ticks() -> u64 { return timer_ticks; }
fn apic_eoi() { if apic_ok { lwr(LAPIC_EOI, 0); } }
fn apic_vector_timer() -> u64 { return VECTOR_TIMER; }
fn apic_timer_irq() { timer_ticks += 1; }

// Dirección y dato de un mensaje MSI dirigido a esta CPU: entrega fija, por flanco.
fn apic_ioapic_mode() -> bool { return ioapic_mode; }
fn ioapic_read(reg: u64) -> u32 { mmio_write32(ioapic, reg as u32); return mmio_read32(ioapic + 0x10); }
fn ioapic_write(reg: u64, v: u32) { mmio_write32(ioapic, reg as u32); mmio_write32(ioapic + 0x10, v); }

// Entrada de redirección de un GSI (82093AA §3.2.4): palabra alta (destino) primero y la baja
// (vector, modos, polaridad, disparo, máscara) después, como hace Linux, para que una entrada
// desenmascarada nunca quede a medio escribir. Entrega fija, destino físico = esta CPU.
fn ioapic_set(gsi: u64, vector: u64, active_low: bool, level: bool, masked: bool) {
    var low: u32 = (vector & 0xFF) as u32;
    if active_low { low = low | (1 << 13); }
    if level { low = low | (1 << 15); }
    if masked { low = low | (1 << 16); }
    ioapic_write(0x11 + 2 * gsi, (apic_id as u32) << 24);
    ioapic_write(0x10 + 2 * gsi, low);
}

// Ruta una IRQ ISA al vector dado: GSI, polaridad y disparo según las redirecciones de la
// MADT (0 = valor por defecto del bus ISA: flanco, activo alto; 1/3 explícitos).
fn apic_route_isa_irq(irq: u64, vector: u64) -> bool {
    if !ioapic_mode { return false; }
    let gsi = acpi_irq_to_gsi(irq);
    if gsi >= ioapic_entries { return false; }
    let flags = acpi_irq_flags(irq);
    let active_low = (flags & 3) == 3;
    let level = ((flags >> 2) & 3) == 3;
    ioapic_set(gsi, vector, active_low, level, false);
    var disparo = "flanco"; if level { disparo = "nivel"; }
    var polaridad = "activo alto"; if active_low { polaridad = "activo bajo"; }
    var nota = ""; if flags != 0 { nota = ", por redireccion de la MADT"; }
    kprintf("[apic] ioapic: irq %d -> gsi %d vector %d (%s, %s%s)\n", irq, gsi, vector, disparo, polaridad, nota);
    return true;
}

// Pasa de modo PIC (cable virtual por LINT0) a modo IOAPIC: enmascara las 24 entradas del
// IOAPIC y las 16 líneas del 8259, deja LINT0 enmascarado como ExtINT (lo que Linux escribe
// en check_timer: «the virtual wire has to be disabled in the local APIC») y ruta teclado
// (IRQ1 -> 33) y ratón (IRQ12 -> 44). El EOI de estas interrupciones va al LAPIC (idt.e).
fn apic_switch_to_ioapic() -> bool {
    if !apic_ok || ioapic == 0 { return false; }
    for i in 0..ioapic_entries { ioapic_write(0x10 + 2 * i, 1 << 16); ioapic_write(0x11 + 2 * i, 0); }
    pic_disable();
    lwr(LAPIC_LVT_LINT0, LVT_MASK | 0x700);
    ioapic_mode = true;
    apic_route_isa_irq(1, 33);
    apic_route_isa_irq(12, 44);
    kprintf("[apic] modo IOAPIC: PIC apagado, LINT0 enmascarado; teclado y raton por el IOAPIC\n");
    return true;
}

fn apic_msi_address() -> u64 { return 0xFEE00000 | (apic_id << 12); }
fn apic_msi_data(vector: u64) -> u64 { return vector & 0xFF; }

// Espera 10 ms con el canal 2 del PIT en modo 0 (un disparo), sin interrupciones.
fn pit_wait_10ms() {
    let gate = inb(0x61);
    outb(0x61, (gate & 0xFD) | 0x01);       // altavoz apagado, puerta del canal 2 abierta
    outb(0x43, 0xB0);                        // canal 2, lo/hi, modo 0
    outb(0x42, (11932 & 0xFF) as u8);        // 1193182 / 100
    outb(0x42, (11932 >> 8) as u8);
    var spins: u64 = 0;
    while (inb(0x61) & 0x20) == 0 && spins < 50000000 { cpu_pause(); spins += 1; }
    outb(0x61, gate);
}

// Calibra el temporizador del LAPIC contra el PIT y lo deja periódico a `hz`. Enmascara la
// IRQ0 del PIC: desde aquí el tick del sistema llega por el vector 48.
fn apic_timer_start(hz: u64) -> bool {
    if !apic_ok || hz == 0 { return false; }
    lwr(LAPIC_TIMER_DIV, 0x3);                                  // divisor 16
    lwr(LAPIC_LVT_TIMER, LVT_MASK | (VECTOR_TIMER as u32));     // enmascarado mientras se mide
    lwr(LAPIC_TIMER_INIT, 0xFFFFFFFF);
    pit_wait_10ms();
    let elapsed = (0xFFFFFFFF - lrd(LAPIC_TIMER_CUR)) as u64;
    lwr(LAPIC_TIMER_INIT, 0);
    if elapsed < 100 || elapsed == 0xFFFFFFFF { kprintf("[apic] temporizador sin cuenta util (%d): sigue el PIT\n", elapsed); return false; }
    timer_per_10ms = elapsed;
    let count = elapsed * 100 / hz;
    timer_hz = hz;
    kprintf("[apic] temporizador: %d cuentas/10ms (div 16, ~%d MHz de bus); periodico a %d Hz\n",
            elapsed, elapsed * 16 / 10000, hz);
    lwr(LAPIC_LVT_TIMER, LVT_PERIODIC | (VECTOR_TIMER as u32));
    lwr(LAPIC_TIMER_INIT, count as u32);
    pic_set_mask(0, true);
    kprintf("[apic] reloj del sistema en el LAPIC; IRQ0 del PIC enmascarada\n");
    return true;
}

// Mapea y habilita el LAPIC (SVR.enable, vector espurio 255, TPR 0) y lee el IOAPIC.
fn apic_init() -> bool {
    apic_ok = false;
    if !acpi_present() { kprintf("[apic] sin ACPI: no hay LAPIC conocido\n"); return false; }
    let base_msr = cpu_rdmsr(IA32_APIC_BASE);
    if (base_msr & (1 << 11)) == 0 { kprintf("[apic] IA32_APIC_BASE sin el LAPIC habilitado (%x)\n", base_msr); return false; }
    let phys = acpi_lapic_addr();
    vmm_map_mmio(KMMIO_LAPIC, phys, 1);
    lapic = KMMIO_LAPIC;
    apic_ok = true;
    lwr(LAPIC_TPR, 0);
    lwr(LAPIC_LVT_ERROR, LVT_MASK);
    lwr(LAPIC_SVR, SVR_ENABLE | (VECTOR_SPURIOUS as u32));
    apic_id = (lrd(LAPIC_ID) >> 24) as u64;
    let version = lrd(LAPIC_VERSION);
    kprintf("[apic] lapic id=%d version=%x lvts=%d base=%x msr=%x bsp=%d\n", apic_id, (version & 0xFF) as u64,
            (((version >> 16) & 0xFF) + 1) as u64, phys, base_msr, ((base_msr >> 8) & 1) as u64);
    if acpi_ioapic_count() > 0 {
        let io_phys = acpi_ioapic_addr(0);
        vmm_map_mmio(KMMIO_IOAPIC, io_phys & 0xFFFFFFFFFFFFF000, 1);
        ioapic = KMMIO_IOAPIC + (io_phys & 0xFFF);
        mmio_write32(ioapic, 0);                 // IOREGSEL = ID
        let id = (mmio_read32(ioapic + 0x10) >> 24) & 0xF;
        mmio_write32(ioapic, 1);                 // IOREGSEL = VER
        let ver = mmio_read32(ioapic + 0x10);
        ioapic_entries = (((ver >> 16) & 0xFF) + 1) as u64;
        ioapic_version = (ver & 0xFF) as u64;
        kprintf("[apic] ioapic id=%d version=%x entradas=%d gsi_base=%d (rutas en apic_switch_to_ioapic)\n",
                id as u64, (ver & 0xFF) as u64, ioapic_entries, acpi_ioapic_gsi_base(0));
    }
    return true;
}
