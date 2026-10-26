// G0 — mecanismo 1 de configuración PCI de x86 (puertos CF8/CFC).
// pci_conf1_read32: sólo se escribe el selector de dirección (auditado: cero escrituras a CFC).
// pci_conf1_write32: LA ÚNICA escritura de datos acotada que hace el kernel, usada sólo por
// pci_enable_bus_master() sobre la NIC que el propio kernel identificó (bits Memory Space y
// Bus Master del registro Command). Nunca es alcanzable desde Ring 3 y se audita aparte
// (exactamente una escritura a CFC, selector restaurado, IF conservado: tests/pci).
// El kernel corre en una CPU; antes del SMP, proteger este par de puertos con un cerrojo
// global además de deshabilitar las interrupciones locales.
// Sustituye a pci_io.h (C) desde el 26/10/2026; mismo código, en Erlan.

const CF8: u16 = 0xCF8;
const CFC: u16 = 0xCFC;

fn pci_conf1_read32(selector: u32) -> u32 {
    var flags: u64 = 0;
    var previous: u32 = 0;
    var value: u32 = 0;
    unsafe {
        asm("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
        asm("inl %1, %0" : "=a"(previous) : "Nd"(CF8) : "memory");
        asm("outl %0, %1" : : "a"(selector), "Nd"(CF8) : "memory");
        asm("inl %1, %0" : "=a"(value) : "Nd"(CFC) : "memory");
        asm("outl %0, %1" : : "a"(previous), "Nd"(CF8) : "memory");
        // IF=0 a la entrada debe seguir deshabilitado, incluido el arranque y los IRQ.
        if (flags & (1 << 9)) != 0 { asm("sti" : : : "memory"); }
    }
    return value;
}

fn pci_conf1_write32(selector: u32, value: u32) {
    var flags: u64 = 0;
    var previous: u32 = 0;
    unsafe {
        asm("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
        asm("inl %1, %0" : "=a"(previous) : "Nd"(CF8) : "memory");
        asm("outl %0, %1" : : "a"(selector), "Nd"(CF8) : "memory");
        asm("outl %0, %1" : : "a"(value), "Nd"(CFC) : "memory");
        asm("outl %0, %1" : : "a"(previous), "Nd"(CF8) : "memory");
        if (flags & (1 << 9)) != 0 { asm("sti" : : : "memory"); }
    }
}
