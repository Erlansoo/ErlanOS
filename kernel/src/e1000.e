// L1 — mecanismo NIC Intel e1000 (82540EM, PCI 8086:100E; el modelo que emula QEMU).
// Anillos de descriptores legacy de 16 bytes, sondeo (sin IRQ), un frame TX en vuelo.
// Es un MECANISMO de L1 como pit.e o keyboard.e: no contiene protocolo. La pila
// (ARP/IP/UDP/DHCP/DNS) vive en net/ y, en su forma final, en un servicio Ring 3.
// Registros y layout verificados contra el SDM Intel 8254x y hw/net/e1000.c de QEMU.
// La familia e1000e (82574L, I219-V) comparte el mapa de registros pero exige un
// init de PHY/PCH distinto: aquí se detecta y se rechaza explícitamente, sin fingir.

import mmio;
import pci;
import pmm;
import vmm;
import kmem;
import serial;

// Registros (offsets en BAR0)
const REG_CTRL: u64 = 0x0000;
const REG_STATUS: u64 = 0x0008;
const REG_ICR: u64 = 0x00C0;
const REG_IMS: u64 = 0x00D0;
const REG_IMC: u64 = 0x00D8;
const REG_RCTL: u64 = 0x0100;
const REG_TCTL: u64 = 0x0400;
const REG_TIPG: u64 = 0x0410;
const REG_RDBAL: u64 = 0x2800;
const REG_RDBAH: u64 = 0x2804;
const REG_RDLEN: u64 = 0x2808;
const REG_RDH: u64 = 0x2810;
const REG_RDT: u64 = 0x2818;
const REG_TDBAL: u64 = 0x3800;
const REG_TDBAH: u64 = 0x3804;
const REG_TDLEN: u64 = 0x3808;
const REG_TDH: u64 = 0x3810;
const REG_TDT: u64 = 0x3818;
const REG_MTA: u64 = 0x5200;
const REG_RAL0: u64 = 0x5400;
const REG_RAH0: u64 = 0x5404;

const CTRL_LRST: u32 = 1 << 3;
const CTRL_ASDE: u32 = 1 << 5;
const CTRL_SLU: u32 = 1 << 6;
const CTRL_ILOS: u32 = 1 << 7;
const CTRL_RST: u32 = 1 << 26;
const CTRL_VME: u32 = 1 << 30;
const CTRL_PHY_RST: u32 = 1 << 31;
const STATUS_LU: u32 = 1 << 1;
const RAH_AV: u32 = 1 << 31;

const RCTL_EN: u32 = 1 << 1;
const RCTL_BAM: u32 = 1 << 15;
const RCTL_SECRC: u32 = 1 << 26;      // el hardware quita el FCS de lo recibido
const TCTL_EN: u32 = 1 << 1;
const TCTL_PSP: u32 = 1 << 3;         // rellena tramas cortas
const TCTL_CT: u32 = 0x0F << 4;
const TCTL_COLD: u32 = 0x40 << 12;
const TIPG_COPPER: u32 = 0x00602008;  // IPGT=8, IPGR1=8, IPGR2=6 (SDM 13.4.34)

const TXD_CMD_EOP: u8 = 1;
const TXD_CMD_IFCS: u8 = 2;           // el hardware inserta el FCS
const TXD_CMD_RS: u8 = 8;             // reportar estado (DD)
const DESC_DD: u8 = 1;
const RXD_EOP: u8 = 2;

const DEV_82540EM: u64 = 0x100E;
const DEV_82574L: u64 = 0x10D3;
const DEV_I219V: u64 = 0x15B8;

const KMMIO_BASE: u64 = 0xFFFFFFFFA0000000;   // ventana de kernel para BAR0, no cacheable
const BAR_PAGES: u64 = 32;                     // 128 KiB
const N_DESC: u64 = 32;                        // por anillo: 512 bytes, una página
const RX_BUF: u64 = 2048;
const MAX_FRAME: u64 = 1514;
const TX_SPIN: u64 = 4000000;

var present: bool = false;
var mmio: u64 = 0;
var mac: [6]u8;
var rx_ring: u64 = 0;       // virtual (HHDM)
var rx_ring_phys: u64 = 0;
var tx_ring: u64 = 0;
var tx_ring_phys: u64 = 0;
var rx_buf_phys: [32]u64;
var tx_buf: u64 = 0;
var tx_buf_phys: u64 = 0;
var rx_next: u64 = 0;
var tx_next: u64 = 0;
var tx_frames: u64 = 0;
var rx_frames: u64 = 0;
var rx_dropped: u64 = 0;

fn rd(off: u64) -> u32 { return mmio_read32(mmio + off); }
fn wr(off: u64, v: u32) { mmio_write32(mmio + off, v); }

fn e1000_present() -> bool { return present; }
fn e1000_link_up() -> bool { if !present { return false; } return (rd(REG_STATUS) & STATUS_LU) != 0; }
fn e1000_tx_count() -> u64 { return tx_frames; }
fn e1000_rx_count() -> u64 { return rx_frames; }

fn e1000_mac(out: []u8) {
    for i in 0..6 { if i < out.len { out[i] = mac[i]; } }
}

fn e1000_init() -> bool {
    present = false;
    if !pci_nic_present() { kprintf("[e1000] sin NIC Intel en PCI\n"); return false; }
    let dev = pci_nic_device();
    if dev != DEV_82540EM {
        kprintf("[e1000] NIC %x es familia e1000e (82574L/I219): init de PHY/PCH no implementado todavia\n", dev);
        return false;
    }
    let bar = pci_nic_bar0() & 0xFFFFFFFFFFFFFFF0;
    if bar == 0 { kprintf("[e1000] BAR0 sin asignar\n"); return false; }

    // Único efecto de escritura en configuración PCI: Memory Space + Bus Master.
    let cmd = pci_enable_bus_master(pci_nic_bus(), pci_nic_slot(), pci_nic_fn());
    kprintf("[e1000] bdf %d:%d.%d bar0=%x command=%x\n", pci_nic_bus(), pci_nic_slot(), pci_nic_fn(), bar, cmd);
    if (cmd & 0x6) != 0x6 { kprintf("[e1000] no se pudo habilitar memoria/bus master\n"); return false; }

    vmm_map_mmio(KMMIO_BASE, bar, BAR_PAGES);
    mmio = KMMIO_BASE;

    // Reset global y silencio de interrupciones.
    wr(REG_IMC, 0xFFFFFFFF);
    wr(REG_CTRL, rd(REG_CTRL) | CTRL_RST);
    var spins: u64 = 0;
    while (rd(REG_CTRL) & CTRL_RST) != 0 && spins < 1000000 { cpu_pause(); spins += 1; }
    if (rd(REG_CTRL) & CTRL_RST) != 0 { kprintf("[e1000] el reset no termino\n"); return false; }
    wr(REG_IMC, 0xFFFFFFFF);
    rd(REG_ICR);

    // Enlace: set-link-up + auto-speed; sin reset de PHY/link ni loopback.
    var ctrl = rd(REG_CTRL);
    ctrl = ctrl & ~(CTRL_LRST | CTRL_PHY_RST | CTRL_ILOS | CTRL_VME);
    ctrl = ctrl | CTRL_SLU | CTRL_ASDE;
    wr(REG_CTRL, ctrl);

    // MAC: QEMU precarga RAL0/RAH0 (52:54:00:12:34:56). Portátil a e1000e/I219.
    let ral = rd(REG_RAL0);
    var rah = rd(REG_RAH0);
    mac[0] = (ral & 0xFF) as u8; mac[1] = ((ral >> 8) & 0xFF) as u8;
    mac[2] = ((ral >> 16) & 0xFF) as u8; mac[3] = ((ral >> 24) & 0xFF) as u8;
    mac[4] = (rah & 0xFF) as u8; mac[5] = ((rah >> 8) & 0xFF) as u8;
    if ral == 0 && (rah & 0xFFFF) == 0 { kprintf("[e1000] RAL0/RAH0 vacios: sin MAC\n"); return false; }
    if (rah & RAH_AV) == 0 { rah = rah | RAH_AV; wr(REG_RAH0, rah); }
    for i in 0..128 { wr(REG_MTA + i * 4, 0); }

    // Anillo RX: 32 descriptores en una página; un buffer de 2 KiB (una página) cada uno.
    rx_ring_phys = pmm_alloc_page();
    rx_ring = pmm_phys_to_virt(rx_ring_phys);
    kmemset(rx_ring, 0, 4096);
    for i in 0..N_DESC {
        rx_buf_phys[i] = pmm_alloc_page();
        kmemset(pmm_phys_to_virt(rx_buf_phys[i]), 0, 4096);
        mmio_write64(rx_ring + i * 16, rx_buf_phys[i]);
        mmio_write32(rx_ring + i * 16 + 8, 0);
        mmio_write32(rx_ring + i * 16 + 12, 0);
    }
    wr(REG_RDBAL, (rx_ring_phys & 0xFFFFFFFF) as u32);
    wr(REG_RDBAH, (rx_ring_phys >> 32) as u32);
    wr(REG_RDLEN, (N_DESC * 16) as u32);
    wr(REG_RDH, 0);
    wr(REG_RDT, (N_DESC - 1) as u32);
    rx_next = 0;
    wr(REG_RCTL, RCTL_EN | RCTL_BAM | RCTL_SECRC);     // BSIZE=2048 (bits 16-17 = 00, BSEX=0)

    // Anillo TX: 32 descriptores en una página; un buffer de trama (una página).
    tx_ring_phys = pmm_alloc_page();
    tx_ring = pmm_phys_to_virt(tx_ring_phys);
    kmemset(tx_ring, 0, 4096);
    tx_buf_phys = pmm_alloc_page();
    tx_buf = pmm_phys_to_virt(tx_buf_phys);
    wr(REG_TDBAL, (tx_ring_phys & 0xFFFFFFFF) as u32);
    wr(REG_TDBAH, (tx_ring_phys >> 32) as u32);
    wr(REG_TDLEN, (N_DESC * 16) as u32);
    wr(REG_TDH, 0);
    wr(REG_TDT, 0);
    tx_next = 0;
    wr(REG_TIPG, TIPG_COPPER);
    wr(REG_TCTL, TCTL_EN | TCTL_PSP | TCTL_CT | TCTL_COLD);

    present = true;
    kprintf("[e1000] listo: mac=%x:%x:%x:%x:%x:%x link=%d rx_ring=%x tx_ring=%x\n",
            mac[0] as u64, mac[1] as u64, mac[2] as u64, mac[3] as u64, mac[4] as u64, mac[5] as u64,
            e1000_link_up() as u64, rx_ring_phys, tx_ring_phys);
    return true;
}

// Envía una trama Ethernet (sin FCS) y espera a que el hardware la consuma.
fn e1000_tx(frame: []u8) -> bool {
    if !present || frame.len == 0 || frame.len > MAX_FRAME { return false; }
    var src: u64 = 0;
    unsafe { src = frame.ptr as u64; }
    kmemcpy(tx_buf, src, frame.len);
    let d = tx_ring + tx_next * 16;
    mmio_write64(d, tx_buf_phys);
    mmio_write16(d + 8, frame.len as u16);
    mmio_write8(d + 10, 0);
    mmio_write8(d + 11, TXD_CMD_EOP | TXD_CMD_IFCS | TXD_CMD_RS);
    mmio_write8(d + 12, 0);
    mmio_write8(d + 13, 0);
    mmio_write16(d + 14, 0);
    cpu_mfence();
    let next = (tx_next + 1) % N_DESC;
    wr(REG_TDT, next as u32);
    var spins: u64 = 0;
    while (mmio_read8(d + 12) & DESC_DD) == 0 && spins < TX_SPIN { cpu_pause(); spins += 1; }
    let done = (mmio_read8(d + 12) & DESC_DD) != 0;
    tx_next = next;
    if done { tx_frames += 1; }
    return done;
}

// Copia la siguiente trama recibida (sin FCS) en out. Devuelve 0 si no hay ninguna.
fn e1000_rx(out: []u8) -> u64 {
    if !present { return 0; }
    let d = rx_ring + rx_next * 16;
    let status = mmio_read8(d + 12);
    if (status & DESC_DD) == 0 { return 0; }
    var len = mmio_read16(d + 8) as u64;
    let errors = mmio_read8(d + 13);
    var copied: u64 = 0;
    if errors == 0 && (status & RXD_EOP) != 0 {
        if len > out.len { len = out.len; }
        var dst: u64 = 0;
        unsafe { dst = out.ptr as u64; }
        kmemcpy(dst, pmm_phys_to_virt(rx_buf_phys[rx_next]), len);
        copied = len;
        rx_frames += 1;
    } else {
        rx_dropped += 1;
    }
    // Devolver el descriptor al hardware: RDT = índice consumido.
    mmio_write32(d + 8, 0);
    mmio_write32(d + 12, 0);
    cpu_mfence();
    wr(REG_RDT, rx_next as u32);
    rx_next = (rx_next + 1) % N_DESC;
    return copied;
}
