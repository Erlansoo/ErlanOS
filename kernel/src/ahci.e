// L1 — mecanismo de disco AHCI (SATA): un puerto, un comando en vuelo, sondeo sin IRQ.
// Es un MECANISMO como e1000.e: lee y escribe sectores de 512 bytes por LBA48 y vacía la
// caché; la política (qué partición, qué proceso) vive en storage.e y en Ring 3.
// Registros y estructuras según la especificación Serial ATA AHCI 1.3.1 (Intel, pública)
// y ATA/ATAPI Command Set (READ/WRITE DMA EXT, FLUSH CACHE EXT, IDENTIFY DEVICE).
// Implementación original en Erlan; Redox ahcid (MIT) y Linux libahci se leyeron como
// referencia (docs/DRIVERS-ESTUDIO.md). Probado en QEMU (ICH9, 8086:2922) y VirtualBox
// (ICH8, 8086:2829); el AHCI del Z170 (8086:a102) es el mismo registro.
import mmio;
import pci;
import pmm;
import vmm;
import kmem;
import serial;
import pci_msi;
import apic;

// Registros genéricos del HBA (offsets en ABAR = BAR5).
const HBA_CAP: u64 = 0x00;
const HBA_GHC: u64 = 0x04;
const HBA_IS: u64 = 0x08;
const HBA_PI: u64 = 0x0C;
const HBA_VS: u64 = 0x10;
const GHC_AE: u32 = 1 << 31;
const CAP_S64A: u32 = 1 << 31;
const CAP_NP_MASK: u32 = 0x1F;

// Registros de puerto (ABAR + 0x100 + 0x80 * puerto).
const PX_CLB: u64 = 0x00;
const PX_CLBU: u64 = 0x04;
const PX_FB: u64 = 0x08;
const PX_FBU: u64 = 0x0C;
const PX_IS: u64 = 0x10;
const PX_IE: u64 = 0x14;
const PX_CMD: u64 = 0x18;
const PX_TFD: u64 = 0x20;
const PX_SIG: u64 = 0x24;
const PX_SSTS: u64 = 0x28;
const PX_SERR: u64 = 0x30;
const HBA_IS_REG: u64 = 0x08;
const GHC_IE: u32 = 1 << 1;
const VECTOR_AHCI: u64 = 49;
const PX_CI: u64 = 0x38;
const CMD_ST: u32 = 1 << 0;
const CMD_FRE: u32 = 1 << 4;
const CMD_FR: u32 = 1 << 14;
const CMD_CR: u32 = 1 << 15;
const TFD_BSY: u32 = 0x80;
const TFD_DRQ: u32 = 0x08;
const TFD_ERR: u32 = 0x01;
const IS_TFES: u32 = 1 << 30;
const SIG_SATA: u32 = 0x00000101;

// Comandos ATA.
const ATA_IDENTIFY: u8 = 0xEC;
const ATA_READ_DMA_EXT: u8 = 0x25;
const ATA_WRITE_DMA_EXT: u8 = 0x35;
const ATA_FLUSH_CACHE_EXT: u8 = 0xEA;
const FIS_H2D: u8 = 0x27;

const KMMIO_AHCI: u64 = 0xFFFFFFFFA0100000;   // ventana no cacheable para el ABAR (8 KiB)
const SPIN: u64 = 20000000;

var present: bool = false;
var abar: u64 = 0;
var port: u64 = 0;            // dirección virtual de los registros del puerto elegido
var port_index: u64 = 0;
var sectors: u64 = 0;
var clb: u64 = 0; var clb_phys: u64 = 0;      // lista de comandos (1 KiB, usamos la entrada 0)
var fb: u64 = 0; var fb_phys: u64 = 0;        // área de FIS recibidos (256 bytes)
var ctba: u64 = 0; var ctba_phys: u64 = 0;    // tabla del comando 0 (CFIS + PRDT)
var buf: u64 = 0; var buf_phys: u64 = 0;      // un sector de datos (DMA)
var msi_on: bool = false;
var irqs: u64 = 0;
var pci_bus: u64 = 0; var pci_slot: u64 = 0; var pci_func: u64 = 0;

fn hba_rd(off: u64) -> u32 { return mmio_read32(abar + off); }
fn hba_wr(off: u64, v: u32) { mmio_write32(abar + off, v); }
fn prd(off: u64) -> u32 { return mmio_read32(port + off); }
fn pwr(off: u64, v: u32) { mmio_write32(port + off, v); }

fn ahci_present() -> bool { return present; }
fn ahci_sectors() -> u64 { return sectors; }
fn ahci_irq_count() -> u64 { return irqs; }
fn ahci_msi_on() -> bool { return msi_on; }
fn ahci_vector() -> u64 { return VECTOR_AHCI; }

// Interrupción MSI del HBA (vector 49): limpia PxIS y el bit del puerto en HBA.IS y cuenta.
// Las esperas siguen sondeando PxCI: la interrupción prueba la ruta MSI -> LAPIC -> IDT.
fn ahci_irq() {
    if !present { return; }
    irqs += 1;
    let is = prd(PX_IS);
    if is != 0 { pwr(PX_IS, is); }
    hba_wr(HBA_IS_REG, (1 << (port_index as u32)) as u32);
    if irqs == 1 { kprintf("[ahci] primera interrupcion MSI por el vector %d (PxIS=%x)\n", VECTOR_AHCI, is); }
}

// Pide MSI al dispositivo y habilita las fuentes de interrupción del puerto y del HBA.
fn ahci_enable_msi() {
    if !pci_enable_msi(pci_bus, pci_slot, pci_func, apic_msi_address(), apic_msi_data(VECTOR_AHCI)) {
        kprintf("[ahci] sin capacidad MSI: completaciones por sondeo\n");
        return;
    }
    pwr(PX_IS, 0xFFFFFFFF);
    pwr(PX_IE, 0x1F);                                 // DHRE, PSE, DSE, SDBE, UFE
    hba_wr(HBA_IS_REG, 0xFFFFFFFF);
    hba_wr(HBA_GHC, hba_rd(HBA_GHC) | GHC_IE);
    msi_on = true;
    kprintf("[ahci] msi activo: vector %d\n", VECTOR_AHCI);
}

// Detiene el motor de comandos y la recepción de FIS del puerto (AHCI 10.1.2).
fn port_stop() -> bool {
    pwr(PX_CMD, prd(PX_CMD) & ~CMD_ST);
    var spins: u64 = 0;
    while (prd(PX_CMD) & CMD_CR) != 0 && spins < SPIN { cpu_pause(); spins += 1; }
    if (prd(PX_CMD) & CMD_CR) != 0 { return false; }
    pwr(PX_CMD, prd(PX_CMD) & ~CMD_FRE);
    spins = 0;
    while (prd(PX_CMD) & CMD_FR) != 0 && spins < SPIN { cpu_pause(); spins += 1; }
    return (prd(PX_CMD) & CMD_FR) == 0;
}

// Arma la cabecera 0 y la tabla del comando con un FIS H2D y un PRDT de un sector.
fn build_command(cmd: u8, lba: u64, write: bool, bytes: u64) {
    // Cabecera de comando 0 (32 bytes): CFL=5 dwords, W, PRDTL=1, CTBA.
    var dw0: u32 = 5 | (1 << 16);
    if write { dw0 = dw0 | (1 << 6); }
    mmio_write32(clb, dw0);
    mmio_write32(clb + 4, 0);                       // PRDBC
    mmio_write32(clb + 8, (ctba_phys & 0xFFFFFFFF) as u32);
    mmio_write32(clb + 12, (ctba_phys >> 32) as u32);
    // Tabla: CFIS (64 bytes) + ACMD (16) + reservado (48) + PRDT en 0x80.
    kmemset(ctba, 0, 256);
    mmio_write8(ctba + 0, FIS_H2D);
    mmio_write8(ctba + 1, 0x80);                    // C=1: registro de comando
    mmio_write8(ctba + 2, cmd);
    mmio_write8(ctba + 4, (lba & 0xFF) as u8);
    mmio_write8(ctba + 5, ((lba >> 8) & 0xFF) as u8);
    mmio_write8(ctba + 6, ((lba >> 16) & 0xFF) as u8);
    mmio_write8(ctba + 7, 0x40);                    // modo LBA
    mmio_write8(ctba + 8, ((lba >> 24) & 0xFF) as u8);
    mmio_write8(ctba + 9, ((lba >> 32) & 0xFF) as u8);
    mmio_write8(ctba + 10, ((lba >> 40) & 0xFF) as u8);
    var count: u64 = 1;
    if bytes == 0 { count = 0; }
    mmio_write8(ctba + 12, count as u8);            // sectores (bajo)
    mmio_write8(ctba + 13, 0);
    if bytes != 0 {
        mmio_write32(ctba + 0x80, (buf_phys & 0xFFFFFFFF) as u32);
        mmio_write32(ctba + 0x84, (buf_phys >> 32) as u32);
        mmio_write32(ctba + 0x88, 0);
        mmio_write32(ctba + 0x8C, ((bytes - 1) & 0x3FFFFF) as u32);
    } else {
        mmio_write32(clb, 5);                       // sin PRDT (FLUSH)
    }
    cpu_mfence();
}

// Emite el comando 0 y espera a que termine; false si hay error de task file o plazo.
fn issue() -> bool {
    var spins: u64 = 0;
    while (prd(PX_TFD) & (TFD_BSY | TFD_DRQ)) != 0 && spins < SPIN { cpu_pause(); spins += 1; }
    if (prd(PX_TFD) & (TFD_BSY | TFD_DRQ)) != 0 { return false; }
    pwr(PX_IS, 0xFFFFFFFF);
    pwr(PX_CI, 1);
    spins = 0;
    while (prd(PX_CI) & 1) != 0 && spins < SPIN {
        if (prd(PX_IS) & IS_TFES) != 0 { return false; }
        cpu_pause(); spins += 1;
    }
    if (prd(PX_CI) & 1) != 0 { return false; }
    if (prd(PX_IS) & IS_TFES) != 0 || (prd(PX_TFD) & TFD_ERR) != 0 { return false; }
    return true;
}

// op: 0 = leer un sector a p, 1 = escribir un sector desde p, 4 = vaciar caché.
fn ahci_transfer(op: u64, lba: u64, p: ptr<u8>) -> bool {
    if !present { return false; }
    if op == 4 {
        build_command(ATA_FLUSH_CACHE_EXT, 0, false, 0);
        let ok = issue();
        if msi_on { kprintf("[ahci] flush ok=%d interrupciones msi=%d\n", ok as u64, irqs); }
        return ok;
    }
    if lba >= sectors || (op != 0 && op != 1) { return false; }
    if op == 1 { unsafe { kmemcpy(buf, p as u64, 512); } cpu_mfence(); }
    var cmd = ATA_READ_DMA_EXT;
    if op == 1 { cmd = ATA_WRITE_DMA_EXT; }
    build_command(cmd, lba, op == 1, 512);
    if !issue() { return false; }
    if op == 0 { cpu_mfence(); unsafe { kmemcpy(p as u64, buf, 512); } }
    return true;
}

fn port_ready(i: u64) -> bool {
    let p = abar + 0x100 + 0x80 * i;
    let ssts = mmio_read32(p + PX_SSTS);
    if (ssts & 0xF) != 3 || ((ssts >> 8) & 0xF) != 1 { return false; }
    return mmio_read32(p + PX_SIG) == SIG_SATA;
}

// Busca el primer controlador AHCI (clase 01.06.01), lo mapea y toma el primer puerto con
// un disco SATA presente. Devuelve false, sin tocar nada más, si no hay ninguno.
fn ahci_init() -> bool {
    present = false;
    var found = false;
    var bus: u64 = 0; var slot: u64 = 0; var func: u64 = 0; var bar5: u64 = 0;
    for b in 0..256 {
        if found { break; }
        for s in 0..32 {
            if found { break; }
            let first = pci_read_device(b, s, 0);
            if !first.present { continue; }
            var functions: u64 = 1;
            if (first.header & 0x80) != 0 { functions = 8; }
            for f in 0..functions {
                let d = pci_read_device(b, s, f);
                if !d.present { continue; }
                if d.class_code == 1 && d.subclass == 6 && d.prog_if == 1 && d.bar_slots == 6 &&
                   (d.bars[5].kind == 2 || d.bars[5].kind == 3) && d.bars[5].assigned {
                    found = true; bus = b; slot = s; func = f; bar5 = d.bars[5].base;
                    pci_bus = b; pci_slot = s; pci_func = f;
                    kprintf("[ahci] bdf %d:%d.%d %x:%x abar=%x\n", b, s, f, d.vendor, d.device, bar5);
                    break;
                }
            }
        }
    }
    if !found { return false; }
    // Memory Space + Bus Master, como para la NIC: sin ellos no hay MMIO ni DMA.
    let cmd = pci_enable_bus_master(bus, slot, func);
    if (cmd & 0x6) != 0x6 { kprintf("[ahci] no se pudo habilitar memoria/bus master\n"); return false; }
    vmm_map_mmio(KMMIO_AHCI, bar5 & 0xFFFFFFFFFFFFF000, 2);
    abar = KMMIO_AHCI + (bar5 & 0xFFF);
    hba_wr(HBA_GHC, hba_rd(HBA_GHC) | GHC_AE);
    let cap = hba_rd(HBA_CAP);
    let pi = hba_rd(HBA_PI);
    kprintf("[ahci] cap=%x pi=%x version=%x puertos=%d s64a=%d\n", cap, pi, hba_rd(HBA_VS), ((cap & CAP_NP_MASK) + 1) as u64, ((cap & CAP_S64A) != 0) as u64);
    var chosen: u64 = 32;
    for i in 0..32 {
        if chosen != 32 { break; }
        if (pi & (1 << (i as u32))) != 0 && port_ready(i) { chosen = i; }
    }
    if chosen == 32 { kprintf("[ahci] sin disco SATA en ningun puerto\n"); return false; }
    port_index = chosen;
    port = abar + 0x100 + 0x80 * chosen;
    if !port_stop() { kprintf("[ahci] el puerto %d no se detiene\n", chosen); return false; }

    clb_phys = pmm_alloc_page(); fb_phys = pmm_alloc_page(); ctba_phys = pmm_alloc_page(); buf_phys = pmm_alloc_page();
    if clb_phys == 0 || fb_phys == 0 || ctba_phys == 0 || buf_phys == 0 { return false; }
    if (cap & CAP_S64A) == 0 && (clb_phys | fb_phys | ctba_phys | buf_phys) > 0xFFFFFFFF {
        kprintf("[ahci] el HBA no direcciona 64 bits y las paginas estan por encima de 4 GiB\n"); return false;
    }
    clb = pmm_phys_to_virt(clb_phys); fb = pmm_phys_to_virt(fb_phys);
    ctba = pmm_phys_to_virt(ctba_phys); buf = pmm_phys_to_virt(buf_phys);
    kmemset(clb, 0, 4096); kmemset(fb, 0, 4096); kmemset(ctba, 0, 4096); kmemset(buf, 0, 4096);
    pwr(PX_CLB, (clb_phys & 0xFFFFFFFF) as u32); pwr(PX_CLBU, (clb_phys >> 32) as u32);
    pwr(PX_FB, (fb_phys & 0xFFFFFFFF) as u32); pwr(PX_FBU, (fb_phys >> 32) as u32);
    pwr(PX_SERR, 0xFFFFFFFF);
    pwr(PX_IS, 0xFFFFFFFF);
    pwr(PX_IE, 0);
    pwr(PX_CMD, prd(PX_CMD) | CMD_FRE);
    pwr(PX_CMD, prd(PX_CMD) | CMD_ST);
    present = true;

    // IDENTIFY DEVICE: capacidad LBA48 en las palabras 100..103.
    build_command(ATA_IDENTIFY, 0, false, 512);
    if !issue() { present = false; kprintf("[ahci] IDENTIFY fallo\n"); return false; }
    sectors = mmio_read64(buf + 200);
    if sectors == 0 { sectors = mmio_read32(buf + 120) as u64; }   // LBA28 (palabras 60..61)
    if sectors == 0 { present = false; kprintf("[ahci] disco sin capacidad\n"); return false; }
    kprintf("[ahci] puerto=%d sectores=%d (%d MiB)\n", chosen, sectors, sectors / 2048);
    if apic_present() { ahci_enable_msi(); }
    return true;
}
