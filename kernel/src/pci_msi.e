// L1 — capacidades PCI, MSI y MSI-X (Message Signaled Interrupts). Un dispositivo con MSI
// escribe un dato en una dirección del LAPIC y la interrupción llega por el vector elegido,
// sin pasar por el PIC ni el IOAPIC. Con MSI-X la dirección y el dato no van en el espacio de
// configuración sino en una tabla en memoria del dispositivo (un BAR). Escrituras de
// configuración acotadas a la lista de capacidades del dispositivo que el kernel identificó
// (igual que pci_enable_bus_master).
// Fuente: PCI Local Bus 3.0 §6.7 (lista de capacidades), §6.8.1 (MSI) y §6.8.2 (MSI-X).
import pci;
import pci_io;
import vmm;
import mmio;
import serial;

const CAP_ID_MSI: u64 = 0x05;
const CAP_ID_MSIX: u64 = 0x11;

fn pci_selector(bus: u64, slot: u64, function: u64, offset: u64) -> u32 {
    return (0x80000000 | (bus << 16) | (slot << 11) | (function << 8) | (offset & 0xFC)) as u32;
}

// Escritura de 32 bits en el espacio de configuración convencional, con los mismos límites
// que pci_read32. Devuelve false si los argumentos no son válidos.
fn pci_write32(bus: u64, slot: u64, function: u64, offset: u64, value: u32) -> bool {
    if bus > 255 || slot > 31 || function > 7 || offset > 252 || (offset & 3) != 0 { return false; }
    pci_conf1_write32(pci_selector(bus, slot, function, offset), value);
    return true;
}

// Offset de la capacidad `id` en la lista del dispositivo (0 si no la tiene).
fn pci_find_capability(bus: u64, slot: u64, function: u64, id: u64) -> u64 {
    let status = (pci_read32(bus, slot, function, 4) >> 16) as u64;
    if (status & 0x10) == 0 { return 0; }                   // sin lista de capacidades
    var p = (pci_read32(bus, slot, function, 0x34) & 0xFC) as u64;
    var hops: u64 = 0;
    while p >= 0x40 && p <= 0xFC && hops < 48 {
        let head = pci_read32(bus, slot, function, p);
        if (head & 0xFF) as u64 == id { return p; }
        p = ((head >> 8) & 0xFC) as u64;
        hops += 1;
    }
    return 0;
}

// Habilita MSI con un solo vector hacia `address`/`data` (ver apic_msi_address/data).
// Devuelve false si el dispositivo no tiene la capacidad. Con MSI activo el dispositivo deja
// de usar su línea INTx (PCI 3.0 §6.8.3.1).
fn pci_enable_msi(bus: u64, slot: u64, function: u64, address: u64, data: u64) -> bool {
    let cap = pci_find_capability(bus, slot, function, CAP_ID_MSI);
    if cap == 0 { return false; }
    let head = pci_read32(bus, slot, function, cap);
    let control = (head >> 16) as u64;
    let wide = (control & 0x80) != 0;                        // direcciones de 64 bits
    pci_write32(bus, slot, function, cap + 4, (address & 0xFFFFFFFF) as u32);
    if wide {
        pci_write32(bus, slot, function, cap + 8, (address >> 32) as u32);
        let low = pci_read32(bus, slot, function, cap + 12) & 0xFFFF0000;
        pci_write32(bus, slot, function, cap + 12, low | (data & 0xFFFF) as u32);
    } else {
        let low = pci_read32(bus, slot, function, cap + 8) & 0xFFFF0000;
        pci_write32(bus, slot, function, cap + 8, low | (data & 0xFFFF) as u32);
    }
    // Multiple Message Enable = 0 (un vector) y Enable = 1; el resto del control se conserva.
    let new_control = (control & ~(0x70 as u64)) | 1;
    pci_write32(bus, slot, function, cap, (head & 0xFFFF) | ((new_control as u32) << 16));
    let check = (pci_read32(bus, slot, function, cap) >> 16) as u64;
    kprintf("[pci] msi bdf %d:%d.%d cap=%x 64bit=%d control=%x -> %x direccion=%x dato=%x\n",
            bus, slot, function, cap, wide as u64, control, check, address, data);
    return (check & 1) != 0;
}

fn pci_has_msix(bus: u64, slot: u64, function: u64) -> bool {
    return pci_find_capability(bus, slot, function, CAP_ID_MSIX) != 0;
}

// Habilita MSI-X con la entrada 0 de la tabla hacia `address`/`data`. La tabla vive en el BAR
// que dice la capacidad (BIR y offset, PCI 3.0 §6.8.2.4); se mapea su página sin caché en
// `window` (4 KiB) y se escribe la entrada (dirección baja, alta, dato, control = 0 sin
// máscara) antes de poner Enable (bit 15) y quitar Function Mask (bit 14). Devuelve false si
// no hay capacidad o el BAR no está asignado.
fn pci_enable_msix(bus: u64, slot: u64, function: u64, address: u64, data: u64, window: u64) -> bool {
    let cap = pci_find_capability(bus, slot, function, CAP_ID_MSIX);
    if cap == 0 { return false; }
    let head = pci_read32(bus, slot, function, cap);
    let control = (head >> 16) as u64;
    let entries = (control & 0x7FF) + 1;
    let table = pci_read32(bus, slot, function, cap + 4) as u64;
    let bir = table & 7;
    let offset = table & ~(7 as u64);
    let d = pci_read_device(bus, slot, function);
    if bir >= d.bar_slots || !(d.bars[bir].kind == 2 || d.bars[bir].kind == 3) || !d.bars[bir].assigned {
        kprintf("[pci] msi-x bdf %d:%d.%d: tabla en BAR%d no asignado\n", bus, slot, function, bir);
        return false;
    }
    let phys = d.bars[bir].base + offset;
    vmm_map_mmio(window, phys & 0xFFFFFFFFFFFFF000, 1);
    let entry = window + (phys & 0xFFF);
    mmio_write32(entry + 0, (address & 0xFFFFFFFF) as u32);
    mmio_write32(entry + 4, (address >> 32) as u32);
    mmio_write32(entry + 8, (data & 0xFFFFFFFF) as u32);
    mmio_write32(entry + 12, 0);                               // Vector Control: sin máscara
    cpu_mfence();
    let new_control = (control | 0x8000) & ~(0x4000 as u64);
    pci_write32(bus, slot, function, cap, (head & 0xFFFF) | ((new_control as u32) << 16));
    let check = (pci_read32(bus, slot, function, cap) >> 16) as u64;
    kprintf("[pci] msi-x bdf %d:%d.%d cap=%x entradas=%d tabla=BAR%d+%x control=%x -> %x direccion=%x dato=%x\n",
            bus, slot, function, cap, entries, bir, offset, control, check, address, data);
    return (check & 0x8000) != 0;
}
