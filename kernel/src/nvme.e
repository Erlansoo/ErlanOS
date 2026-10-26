// L1 — mecanismo de disco NVMe: un controlador, un espacio de nombres, una cola de E/S de
// profundidad 32, un comando en vuelo, completaciones por sondeo del bit de fase (la
// interrupción MSI-X/MSI se cuenta como evidencia de la ruta). Es un MECANISMO como ahci.e:
// lee y escribe sectores de 512 bytes por LBA y vacía la caché; la política (qué partición,
// qué proceso) vive en storage.e y en Ring 3.
// Registros y estructuras según NVM Express Base 2.0 (§3.1 registros, §3.3 colas, §4.1
// entradas de 64/16 bytes, §5 comandos de administración) y NVM Command Set 1.0 (Read,
// Write, Flush). Implementación original en Erlan. De Linux drivers/nvme/host/{pci,core}.c
// (GPL-2.0, sólo leído) se tomó la secuencia y sus plazos: CSTS=0xFFFFFFFF es «no hay
// dispositivo»; CAP se lee de nuevo tras tocar CC; MPSMIN debe ser 4 KiB; CC se escribe sin
// EN (CSS, MPS, AMS, IOSQES=6, IOCQES=4) y luego con EN; CSTS.RDY se sondea cada 1 ms con
// plazo (CAP.TO+1)/2 s; la CQ de E/S se crea antes que la SQ; las identificaciones se piden
// con buffers de 4 KiB; CID con contador de generación. Probado en QEMU (1b36:0010) y VirtualBox (NVMe); el Lexar NM790
// (1d97:1602) del Z170 es el objetivo real.
import mmio;
import pci;
import pmm;
import vmm;
import kmem;
import serial;
import pci_msi;
import apic;
import pit;

// Registros del controlador (offsets en BAR0).
const REG_CAP: u64 = 0x00;        // 64 bits
const REG_VS: u64 = 0x08;
const REG_INTMS: u64 = 0x0C;
const REG_INTMC: u64 = 0x10;
const REG_CC: u64 = 0x14;
const REG_CSTS: u64 = 0x1C;
const REG_AQA: u64 = 0x24;
const REG_ASQ: u64 = 0x28;        // 64 bits
const REG_ACQ: u64 = 0x30;        // 64 bits
const DOORBELLS: u64 = 0x1000;
const CC_EN: u32 = 1;
const CC_SHN_MASK: u32 = 3 << 14;
const CC_IOSQES: u32 = 6 << 16;   // entradas de envío de 64 bytes
const CC_IOCQES: u32 = 4 << 20;   // entradas de completación de 16 bytes
const CSTS_RDY: u32 = 1;
const CSTS_CFS: u32 = 2;

// Comandos de administración (§5) y del conjunto NVM.
const ADMIN_CREATE_SQ: u64 = 0x01;
const ADMIN_CREATE_CQ: u64 = 0x05;
const ADMIN_IDENTIFY: u64 = 0x06;
const ADMIN_SET_FEATURES: u64 = 0x09;
const NVM_FLUSH: u64 = 0x00;
const NVM_WRITE: u64 = 0x01;
const NVM_READ: u64 = 0x02;
const FEAT_NUMBER_OF_QUEUES: u64 = 0x07;

const DEPTH: u64 = 32;            // entradas por cola (nv_admin y E/S); AQA y Create *Q llevan DEPTH-1
const KMMIO_NVME: u64 = 0xFFFFFFFFA0300000;      // BAR0: registros + timbres (hasta 64 páginas)
const KMMIO_NVME_MSIX: u64 = 0xFFFFFFFFA0380000; // página de la tabla MSI-X
const VECTOR_NVME: u64 = 50;
const SPIN: u64 = 20000000;
const STATUS_TIMEOUT: u64 = 0xFFFF;

var present: bool = false;
var bar: u64 = 0;
var dstrd: u64 = 0;
var sectors: u64 = 0;
var nsid: u64 = 1;
var asq: u64 = 0; var asq_phys: u64 = 0;
var acq: u64 = 0; var acq_phys: u64 = 0;
var iosq: u64 = 0; var iosq_phys: u64 = 0;
var iocq: u64 = 0; var iocq_phys: u64 = 0;
var buf: u64 = 0; var buf_phys: u64 = 0;
var ident: u64 = 0; var ident_phys: u64 = 0;
var sq_tail: [2]u64;
var cq_head: [2]u64;
var cq_phase: [2]u64;
var next_cid: u64 = 0;
var irqs: u64 = 0;
var irq_mode: u64 = 0;            // 0 sondeo, 1 MSI, 2 MSI-X
var last_status: u64 = 0;
var pci_bus: u64 = 0; var pci_slot: u64 = 0; var pci_func: u64 = 0;

fn nv_rd32(off: u64) -> u32 { return mmio_read32(bar + off); }
fn nv_wr32(off: u64, v: u32) { mmio_write32(bar + off, v); }
// Los registros de 64 bits se leen y escriben como dos de 32 (la especificación lo permite y
// no depende de que la emulación acepte accesos de 8 bytes).
fn nv_rd64(off: u64) -> u64 { return (nv_rd32(off) as u64) | ((nv_rd32(off + 4) as u64) << 32); }
fn nv_wr64(off: u64, v: u64) { nv_wr32(off, (v & 0xFFFFFFFF) as u32); nv_wr32(off + 4, (v >> 32) as u32); }

fn nvme_present() -> bool { return present; }
fn nvme_sectors() -> u64 { return sectors; }
fn nvme_irq_count() -> u64 { return irqs; }
fn nvme_vector() -> u64 { return VECTOR_NVME; }
fn nvme_last_status() -> u64 { return last_status; }

// Interrupción (vector 50): no hay registro que limpiar; el timbre de la CQ es el acuse.
fn nvme_irq() {
    if !present { return; }
    irqs += 1;
    if irqs == 1 { kprintf("[nvme] primera interrupcion por el vector %d\n", VECTOR_NVME); }
}

// Timbres: SQ tail en 0x1000 + (2*qid) * (4 << DSTRD); CQ head en la entrada siguiente.
fn nv_ring_sq(qid: u64) { nv_wr32(DOORBELLS + (2 * qid) * (4 << dstrd), sq_tail[qid] as u32); }
fn nv_ring_cq(qid: u64) { nv_wr32(DOORBELLS + (2 * qid + 1) * (4 << dstrd), cq_head[qid] as u32); }

fn nv_sq_base(qid: u64) -> u64 { if qid == 0 { return asq; } return iosq; }
fn nv_cq_base(qid: u64) -> u64 { if qid == 0 { return acq; } return iocq; }

// Envía una orden por la cola `qid` y espera su completación por sondeo. Devuelve el campo
// de estado (0 = bien; STATUS_TIMEOUT si no llega). El resultado (DW0) queda en `nv_result`.
var nv_result: u64 = 0;
fn nv_command(qid: u64, opcode: u64, ns: u64, prp1: u64, prp2: u64, dw10: u64, dw11: u64, dw12: u64) -> u64 {
    next_cid = (next_cid + 1) & 0xFFFF;
    let cid = next_cid;
    let e = nv_sq_base(qid) + sq_tail[qid] * 64;
    kmemset(e, 0, 64);
    mmio_write32(e + 0, (opcode | (cid << 16)) as u32);     // DW0: opcode, FUSE=0, PSDT=0 (PRP), CID
    mmio_write32(e + 4, ns as u32);
    mmio_write64(e + 24, prp1);
    mmio_write64(e + 32, prp2);
    mmio_write32(e + 40, dw10 as u32);
    mmio_write32(e + 44, dw11 as u32);
    mmio_write32(e + 48, dw12 as u32);
    cpu_mfence();
    sq_tail[qid] = (sq_tail[qid] + 1) % DEPTH;
    nv_ring_sq(qid);
    // Completación: la entrada en cq_head cambia de fase cuando el controlador la escribe.
    let c = nv_cq_base(qid) + cq_head[qid] * 16;
    var spins: u64 = 0;
    var dw3: u32 = mmio_read32(c + 12);
    while ((dw3 >> 16) & 1) as u64 != cq_phase[qid] && spins < SPIN { cpu_pause(); spins += 1; dw3 = mmio_read32(c + 12); }
    if ((dw3 >> 16) & 1) as u64 != cq_phase[qid] { last_status = STATUS_TIMEOUT; return STATUS_TIMEOUT; }
    cpu_mfence();
    nv_result = mmio_read32(c + 0) as u64;
    let got_cid = (dw3 & 0xFFFF) as u64;
    let status = (dw3 >> 17) as u64;
    cq_head[qid] += 1;
    if cq_head[qid] == DEPTH { cq_head[qid] = 0; cq_phase[qid] = cq_phase[qid] ^ 1; }
    nv_ring_cq(qid);
    if got_cid != cid { kprintf("[nvme] completacion con CID %d, se esperaba %d\n", got_cid, cid); last_status = 0xFFFE; return 0xFFFE; }
    last_status = status;
    return status;
}

fn nv_admin(opcode: u64, ns: u64, prp1: u64, dw10: u64, dw11: u64) -> u64 { return nv_command(0, opcode, ns, prp1, 0, dw10, dw11, 0); }

// Espera a que CSTS.RDY valga `ready`, sondeando cada 1 ms hasta `ms` milisegundos.
fn nv_wait_ready(ready: bool, ms: u64) -> bool {
    var want: u32 = 0;
    if ready { want = CSTS_RDY; }
    let tries = ms + 1;
    for t in 0..tries {
        let csts = nv_rd32(REG_CSTS);
        if csts == 0xFFFFFFFF { return false; }
        if (csts & CSTS_CFS) != 0 { kprintf("[nvme] CSTS.CFS: fallo fatal del controlador\n"); return false; }
        if (csts & CSTS_RDY) == want { return true; }
        pit_sleep_ms(1);
    }
    return false;
}

// Dirección virtual (por el HHDM) de una página física recién pedida, puesta a cero.
fn nv_prepare(p: u64) -> u64 {
    if p == 0 { return 0; }
    let v = pmm_phys_to_virt(p);
    kmemset(v, 0, 4096);
    return v;
}

// op: 0 = leer un sector a p, 1 = escribir un sector desde p, 4 = vaciar caché.
fn nvme_transfer(op: u64, lba: u64, p: ptr<u8>) -> bool {
    if !present { return false; }
    if op == 4 {
        let st = nv_command(1, NVM_FLUSH, nsid, 0, 0, 0, 0, 0);
        if irq_mode != 0 { kprintf("[nvme] flush estado=%d interrupciones=%d\n", st, irqs); }
        return st == 0;
    }
    if lba >= sectors || (op != 0 && op != 1) { return false; }
    if op == 1 { unsafe { kmemcpy(buf, p as u64, 512); } cpu_mfence(); }
    var opcode = NVM_READ;
    if op == 1 { opcode = NVM_WRITE; }
    // DW10/11 = LBA inicial, DW12 = NLB (0 = un bloque); PRP1 = el sector, dentro de una página.
    let st = nv_command(1, opcode, nsid, buf_phys, 0, lba & 0xFFFFFFFF, lba >> 32, 0);
    if st != 0 { kprintf("[nvme] %s lba=%d estado=%x\n", "e/s", lba, st); return false; }
    if op == 0 { cpu_mfence(); unsafe { kmemcpy(p as u64, buf, 512); } }
    return true;
}

// Busca el primer controlador NVMe (clase 01.08.02), lo reinicia, crea la cola de
// administración, identifica controlador y espacio de nombres 1, y crea una cola de E/S.
// Devuelve false, sin tocar nada más, si no hay ninguno o algo no cuadra.
fn nvme_init() -> bool {
    present = false; irqs = 0; irq_mode = 0;
    var found = false;
    var bus: u64 = 0; var slot: u64 = 0; var func: u64 = 0; var bar0: u64 = 0;
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
                if d.class_code == 1 && d.subclass == 8 && d.prog_if == 2 && d.bar_slots >= 1 &&
                   (d.bars[0].kind == 2 || d.bars[0].kind == 3) && d.bars[0].assigned {
                    found = true; bus = b; slot = s; func = f; bar0 = d.bars[0].base;
                    pci_bus = b; pci_slot = s; pci_func = f;
                    kprintf("[nvme] bdf %d:%d.%d %x:%x bar0=%x\n", b, s, f, d.vendor, d.device, bar0);
                    break;
                }
            }
        }
    }
    if !found { return false; }
    let cmd = pci_enable_bus_master(bus, slot, func);
    if (cmd & 0x6) != 0x6 { kprintf("[nvme] no se pudo habilitar memoria/bus master\n"); return false; }
    // Registros + timbres de las colas 0 y 1: 4 páginas bastan hasta DSTRD=9; se mapea más si hace falta.
    vmm_map_mmio(KMMIO_NVME, bar0 & 0xFFFFFFFFFFFFF000, 4);
    bar = KMMIO_NVME + (bar0 & 0xFFF);
    if nv_rd32(REG_CSTS) == 0xFFFFFFFF { kprintf("[nvme] CSTS=0xFFFFFFFF: el dispositivo no responde\n"); return false; }
    var cap = nv_rd64(REG_CAP);
    let mqes = (cap & 0xFFFF) + 1;
    let to_ms = (((cap >> 24) & 0xFF) + 1) * 500;
    dstrd = (cap >> 32) & 0xF;
    let mpsmin = (cap >> 48) & 0xF;
    let vs = nv_rd32(REG_VS) as u64;
    kprintf("[nvme] version=%d.%d mqes=%d dstrd=%d plazo=%d ms mpsmin=%d css=%x\n",
            vs >> 16, (vs >> 8) & 0xFF, mqes, dstrd, to_ms, mpsmin, (cap >> 37) & 0xFF);
    if mpsmin != 0 { kprintf("[nvme] pagina minima mayor que 4 KiB: no soportado\n"); return false; }
    if dstrd > 9 {
        let pages = (DOORBELLS + 4 * (4 << dstrd) + 4095) / 4096;
        if pages > 64 { kprintf("[nvme] DSTRD=%d demasiado grande\n", dstrd); return false; }
        vmm_map_mmio(KMMIO_NVME, bar0 & 0xFFFFFFFFFFFFF000, pages);
    }
    // Reinicio: EN=0 (sin apagado ordenado) y esperar RDY=0.
    let cc_old = nv_rd32(REG_CC);
    nv_wr32(REG_CC, cc_old & ~(CC_EN | CC_SHN_MASK));
    if !nv_wait_ready(false, to_ms) { kprintf("[nvme] el controlador no se deshabilita en %d ms\n", to_ms); return false; }
    asq_phys = pmm_alloc_page(); acq_phys = pmm_alloc_page(); iosq_phys = pmm_alloc_page();
    iocq_phys = pmm_alloc_page(); buf_phys = pmm_alloc_page(); ident_phys = pmm_alloc_page();
    asq = nv_prepare(asq_phys); acq = nv_prepare(acq_phys); iosq = nv_prepare(iosq_phys);
    iocq = nv_prepare(iocq_phys); buf = nv_prepare(buf_phys); ident = nv_prepare(ident_phys);
    if asq == 0 || acq == 0 || iosq == 0 || iocq == 0 || buf == 0 || ident == 0 { return false; }
    for q in 0..2 { sq_tail[q] = 0; cq_head[q] = 0; cq_phase[q] = 1; }
    nv_wr32(REG_AQA, ((DEPTH - 1) | ((DEPTH - 1) << 16)) as u32);
    nv_wr64(REG_ASQ, asq_phys);
    nv_wr64(REG_ACQ, acq_phys);
    // CC sin EN primero (CSS=NVM, MPS=0 → 4 KiB, AMS=RR, tamaños de entrada), luego EN.
    let cc: u32 = CC_IOSQES | CC_IOCQES;
    nv_wr32(REG_CC, cc);
    cap = nv_rd64(REG_CAP);                                     // puede cambiar tras escribir CC (Linux)
    nv_wr32(REG_CC, cc | CC_EN);
    if !nv_wait_ready(true, to_ms) { kprintf("[nvme] el controlador no se habilita en %d ms (CSTS=%x)\n", to_ms, nv_rd32(REG_CSTS) as u64); return false; }
    present = true;
    // Identify Controller (CNS=1): serie/modelo en bytes 4..63, MDTS en 77, NN en 516, VWC en 525.
    var st = nv_admin(ADMIN_IDENTIFY, 0, ident_phys, 1, 0);
    if st != 0 { kprintf("[nvme] Identify Controller estado=%x\n", st); present = false; return false; }
    let mdts = mmio_read8(ident + 77) as u64;
    let nn = mmio_read32(ident + 516) as u64;
    let vwc = mmio_read8(ident + 525) as u64;
    let sqes = mmio_read8(ident + 512) as u64;
    let cqes = mmio_read8(ident + 513) as u64;
    kprintf("[nvme] controlador: vid=%x nn=%d mdts=%d vwc=%d sqes=%x cqes=%x\n",
            mmio_read16(ident + 0) as u64, nn, mdts, vwc & 1, sqes, cqes);
    if nn == 0 { kprintf("[nvme] sin espacios de nombres\n"); present = false; return false; }
    // Identify Namespace (CNS=0, NSID=1): NSZE, NCAP, FLBAS y la tabla LBAF.
    st = nv_admin(ADMIN_IDENTIFY, nsid, ident_phys, 0, 0);
    if st != 0 { kprintf("[nvme] Identify Namespace estado=%x\n", st); present = false; return false; }
    let nsze = mmio_read64(ident + 0);
    let ncap = mmio_read64(ident + 8);
    let flbas = mmio_read8(ident + 26) as u64;
    let lbaf = 128 + 4 * (flbas & 0xF);
    let lbads = mmio_read8(ident + lbaf + 2) as u64;
    let ms = mmio_read16(ident + lbaf) as u64;
    kprintf("[nvme] ns %d: nsze=%d ncap=%d flbas=%x lbads=%d (%d bytes) ms=%d\n", nsid, nsze, ncap, flbas, lbads, (1 as u64) << lbads, ms);
    if nsze == 0 || ncap == 0 { kprintf("[nvme] espacio de nombres sin capacidad\n"); present = false; return false; }
    if lbads != 9 || ms != 0 { kprintf("[nvme] sector de %d bytes con %d de metadatos: solo 512 sin metadatos por ahora\n", (1 as u64) << lbads, ms); present = false; return false; }
    sectors = nsze;
    // Una cola de E/S: Set Features Number of Queues (0 = una), Create CQ 1 (PC, IEN, IV 0), Create SQ 1 (PC, CQID 1).
    st = nv_admin(ADMIN_SET_FEATURES, 0, 0, FEAT_NUMBER_OF_QUEUES, 0);
    if st != 0 { kprintf("[nvme] Set Features Number of Queues estado=%x\n", st); present = false; return false; }
    st = nv_admin(ADMIN_CREATE_CQ, 0, iocq_phys, 1 | ((DEPTH - 1) << 16), 1 | 2);
    if st != 0 { kprintf("[nvme] Create I/O CQ estado=%x\n", st); present = false; return false; }
    st = nv_admin(ADMIN_CREATE_SQ, 0, iosq_phys, 1 | ((DEPTH - 1) << 16), 1 | (1 << 16));
    if st != 0 { kprintf("[nvme] Create I/O SQ estado=%x\n", st); present = false; return false; }
    kprintf("[nvme] listo: %d sectores (%d MiB), colas de %d entradas\n", sectors, sectors / 2048, DEPTH);
    if apic_present() {
        if pci_enable_msix(bus, slot, func, apic_msi_address(), apic_msi_data(VECTOR_NVME), KMMIO_NVME_MSIX) { irq_mode = 2; }
        else if pci_enable_msi(bus, slot, func, apic_msi_address(), apic_msi_data(VECTOR_NVME)) { irq_mode = 1; nv_wr32(REG_INTMC, 1); }
        if irq_mode != 0 { kprintf("[nvme] interrupciones por %s, vector %d\n", "msi-x/msi", VECTOR_NVME); }
        else { kprintf("[nvme] sin MSI-X ni MSI: completaciones por sondeo\n"); }
    }
    return true;
}
