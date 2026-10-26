// G0: inventory only. Segment 0, conventional 256-byte config space, x86 CONF1.
// Read assigned BARs; never size/reassign BARs, enable decoding/bus mastering,
// map MMIO, submit GPU commands, start DMA, or change firmware bus numbering.
// Streaming enumeration has no fixed inventory capacity and no hidden truncation.
// Register encodings cross-checked against Linux include/uapi/linux/pci_regs.h;
// implementation is original Erlan (no dependency on Linux code).
import pci_io;
import serial;

// Datos planos: se copian (@copy), como los registros que describen.
@copy struct PciBar {
    // 0 absent/base-zero, 1 IO, 2 mem32, 3 mem64, 4 legacy mem20,
    // 5 reserved type, 6 upper half, 7 missing upper half, 8 malformed.
    kind: u64;
    base: u64;
    raw_low: u64;
    raw_high: u64;
    assigned: bool;
    prefetch: bool;
}

@copy struct PciDevice {
    present: bool;
    bus: u64;
    slot: u64;
    function: u64;
    vendor: u64;
    device: u64;
    class_code: u64;
    subclass: u64;
    prog_if: u64;
    revision: u64;
    header: u64;
    command: u64;
    status: u64;
    irq_line: u64;
    irq_pin: u64;
    primary: u64;
    secondary: u64;
    subordinate: u64;
    bar_slots: u64;
    bars: [6]PciBar;
}

var pci_devices: u64 = 0;
var pci_gp107: u64 = 0;
var pci_i219: u64 = 0;
var pci_bad_bars: u64 = 0;
var pci_displays: u64 = 0;
var pci_networks: u64 = 0;
// NIC objetivo para el driver e1000 (kernel): el primer Intel 100E/10D3/15B8 encontrado.
var nic_present: bool = false;
var nic_bus: u64 = 0;
var nic_slot: u64 = 0;
var nic_fn: u64 = 0;
var nic_device: u64 = 0;
var nic_bar0: u64 = 0;
var nic_irq: u64 = 0;

// u64 arguments are checked BEFORE narrowing. Invalid BDF, extended offsets,
// and unaligned accesses return all ones without touching ports.
fn pci_read32(bus: u64, slot: u64, function: u64, offset: u64) -> u32 {
    if bus > 255 || slot > 31 || function > 7 || offset > 252 || (offset & 3) != 0 {
        return 0xFFFFFFFF;
    }
    let selector = 0x80000000 | (bus << 16) | (slot << 11) | (function << 8) | offset;
    return pci_conf1_read32(selector as u32);
}

fn pci_bar_invalid(kind: u64) -> bool { return kind == 5 || kind == 7 || kind == 8; }

// Query by BDF rather than a bounded cache index. Missing devices return
// present=false and zeroed fields. Callers must test present before using them.
fn pci_read_device(bus: u64, slot: u64, function: u64) -> PciDevice {
    var d: PciDevice;
    let identity = pci_read32(bus, slot, function, 0) as u64;
    if (identity & 0xFFFF) == 0xFFFF { return d; }
    d.present = true;
    d.bus = bus; d.slot = slot; d.function = function;
    d.vendor = identity & 0xFFFF; d.device = identity >> 16;
    let state = pci_read32(bus, slot, function, 4) as u64;
    d.command = state & 0xFFFF; d.status = state >> 16;
    let classrev = pci_read32(bus, slot, function, 8) as u64;
    d.class_code = classrev >> 24; d.subclass = (classrev >> 16) & 0xFF;
    d.prog_if = (classrev >> 8) & 0xFF; d.revision = classrev & 0xFF;
    d.header = ((pci_read32(bus, slot, function, 12) as u64) >> 16) & 0xFF;
    let irq = pci_read32(bus, slot, function, 60) as u64;
    d.irq_line = irq & 0xFF; d.irq_pin = (irq >> 8) & 0xFF;
    let header_kind = d.header & 0x7F;
    if header_kind == 0 { d.bar_slots = 6; }
    if header_kind == 1 {
        d.bar_slots = 2;
        let buses = pci_read32(bus, slot, function, 24) as u64;
        d.primary = buses & 0xFF; d.secondary = (buses >> 8) & 0xFF;
        d.subordinate = (buses >> 16) & 0xFF;
    }
    // CardBus and unknown layouts are reported but are not decoded as type 0.
    var index: u64 = 0;
    while index < d.bar_slots {
        let raw = pci_read32(bus, slot, function, 16 + index * 4) as u64;
        d.bars[index].raw_low = raw;
        if raw == 0 { index += 1; continue; }
        if (raw & 1) != 0 {
            d.bars[index].kind = 1;
            d.bars[index].base = raw & 0xFFFFFFFC;
            if (raw & 2) != 0 { d.bars[index].kind = 8; }
        } else {
            d.bars[index].prefetch = (raw & 8) != 0;
            d.bars[index].base = raw & 0xFFFFFFF0;
            let mem_type = (raw >> 1) & 3;
            if mem_type == 0 { d.bars[index].kind = 2; }
            if mem_type == 1 {
                d.bars[index].kind = 4;
                if d.bars[index].base > 0xFFFFF { d.bars[index].kind = 8; }
            }
            if mem_type == 2 {
                if index + 1 >= d.bar_slots {
                    d.bars[index].kind = 7;
                } else {
                    let high = pci_read32(bus, slot, function, 20 + index * 4) as u64;
                    d.bars[index].kind = 3;
                    d.bars[index].raw_high = high;
                    d.bars[index].base = d.bars[index].base | (high << 32);
                    d.bars[index + 1].kind = 6;
                    d.bars[index + 1].raw_low = high;
                }
            }
            if mem_type == 3 { d.bars[index].kind = 5; }
        }
        d.bars[index].assigned = d.bars[index].base != 0 && !pci_bar_invalid(d.bars[index].kind);
        if d.bars[index].kind == 3 { index += 1; }
        index += 1;
    }
    return d;
}

fn pci_report(d: PciDevice) {
    pci_devices += 1;
    if d.class_code == 3 { pci_displays += 1; }
    if d.class_code == 2 { pci_networks += 1; }
    // GPU objetivo: GTX 1050 Ti (GP107, 1C82) o la RTX 3090 (GA102, 2204) que hay desde octubre de 2026.
    if d.vendor == 0x10DE && (d.device == 0x1C82 || d.device == 0x2204) { pci_gp107 += 1; }
    if d.vendor == 0x8086 && d.device == 0x15B8 { pci_i219 += 1; }
    if d.vendor == 0x8086 && (d.device == 0x100E || d.device == 0x10D3 || d.device == 0x15B8) && !nic_present {
        // BAR0 de memoria (mem32 o mem64) asignado por el firmware; no se reprograma.
        if d.bar_slots > 0 && (d.bars[0].kind == 2 || d.bars[0].kind == 3) && d.bars[0].assigned {
            nic_present = true;
            nic_bus = d.bus; nic_slot = d.slot; nic_fn = d.function;
            nic_device = d.device; nic_bar0 = d.bars[0].base; nic_irq = d.irq_line;
        }
    }
    kprintf("[pci] dev bus=%d slot=%d fn=%d vendor=%x device=%x class=%x subclass=%x prog_if=%x rev=%x header=%x command=%x status=%x irq_line=%d irq_pin=%d\n",
            d.bus, d.slot, d.function, d.vendor, d.device, d.class_code, d.subclass,
            d.prog_if, d.revision, d.header, d.command, d.status, d.irq_line, d.irq_pin);
    if (d.header & 0x7F) == 1 {
        kprintf("[pci] bridge bus=%d slot=%d fn=%d primary=%d secondary=%d subordinate=%d\n",
                d.bus, d.slot, d.function, d.primary, d.secondary, d.subordinate);
    }
    for index in 0..d.bar_slots {
        let b = d.bars[index];
        if pci_bar_invalid(b.kind) { pci_bad_bars += 1; }
        if b.assigned || pci_bar_invalid(b.kind) {
            kprintf("[pci] bar bus=%d slot=%d fn=%d index=%d kind=%d base=%x prefetch=%d raw_low=%x raw_high=%x\n",
                    d.bus, d.slot, d.function, index, b.kind, b.base,
                    b.prefetch as u64, b.raw_low, b.raw_high);
        }
    }
}

fn pci_count() -> u64 { return pci_devices; }
fn pci_nic_present() -> bool { return nic_present; }
fn pci_nic_bus() -> u64 { return nic_bus; }
fn pci_nic_slot() -> u64 { return nic_slot; }
fn pci_nic_fn() -> u64 { return nic_fn; }
fn pci_nic_device() -> u64 { return nic_device; }
fn pci_nic_bar0() -> u64 { return nic_bar0; }
fn pci_nic_irq_line() -> u64 { return nic_irq; }

// La ÚNICA escritura de configuración PCI del kernel: Memory Space (bit 1) y
// Bus Master (bit 2) del registro Command del dispositivo indicado. Sin esto el
// e1000 no decodifica MMIO ni hace DMA. Devuelve el Command resultante.
fn pci_enable_bus_master(bus: u64, slot: u64, function: u64) -> u64 {
    if bus > 255 || slot > 31 || function > 7 { return 0; }
    let selector = (0x80000000 | (bus << 16) | (slot << 11) | (function << 8) | 4) as u32;
    let before = pci_conf1_read32(selector);
    let after = before | 0x6;
    if after != before { pci_conf1_write32(selector, after); }
    return (pci_conf1_read32(selector) & 0xFFFF) as u64;
}
fn pci_gp107_count() -> u64 { return pci_gp107; }
fn pci_i219_count() -> u64 { return pci_i219; }
fn pci_invalid_bar_count() -> u64 { return pci_bad_bars; }
fn pci_target_gpu_count() -> u64 { return pci_gp107; }
fn pci_target_nic_count() -> u64 { return pci_i219; }
fn pci_display_count() -> u64 { return pci_displays; }
fn pci_network_count() -> u64 { return pci_networks; }

fn pci_init() {
    pci_devices = 0; pci_gp107 = 0; pci_i219 = 0; pci_bad_bars = 0;
    nic_present = false; nic_bus = 0; nic_slot = 0; nic_fn = 0; nic_device = 0; nic_bar0 = 0; nic_irq = 0;
    pci_displays = 0; pci_networks = 0;
    kprintf("[pci] begin segment=0 buses=256 mode=conf1-readonly\n");
    for bus in 0..256 {
        for slot in 0..32 {
            let first = pci_read_device(bus, slot, 0);
            if !first.present { continue; }
            pci_report(first);
            if (first.header & 0x80) != 0 {
                for function in 1..8 {
                    let extra = pci_read_device(bus, slot, function);
                    if extra.present { pci_report(extra); }
                }
            }
        }
    }
    kprintf("[pci] end devices=%d nvidia_gp107=%d intel_i219=%d invalid_bars=%d\n",
            pci_devices, pci_gp107, pci_i219, pci_bad_bars);
}
