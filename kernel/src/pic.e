// L1 — controlador de interrupciones 8259 (PIC legado). Atiende el arranque hasta que apic.e
// pasa a modo IOAPIC (`pic_disable`); sin LAPIC/IOAPIC (probe_hardware) sigue mandando él.

import cpu;
import serial;

const PIC1_CMD: u16 = 0x20;
const PIC1_DATA: u16 = 0x21;
const PIC2_CMD: u16 = 0xA0;
const PIC2_DATA: u16 = 0xA1;
const PIC_EOI: u8 = 0x20;

// Remapea IRQ0-15 a los vectores 32-47 y deja habilitados sólo timer y teclado.
fn pic_init() {
    outb(PIC1_CMD, 0x11); io_wait();   // ICW1: init + ICW4
    outb(PIC2_CMD, 0x11); io_wait();
    outb(PIC1_DATA, 0x20); io_wait();  // ICW2: offset maestro = 32
    outb(PIC2_DATA, 0x28); io_wait();  // ICW2: offset esclavo = 40
    outb(PIC1_DATA, 0x04); io_wait();  // ICW3: esclavo en IRQ2
    outb(PIC2_DATA, 0x02); io_wait();
    outb(PIC1_DATA, 0x01); io_wait();  // ICW4: modo 8086
    outb(PIC2_DATA, 0x01); io_wait();
    outb(PIC1_DATA, 0xFC);             // máscara: sólo IRQ0 (timer) e IRQ1 (teclado)
    outb(PIC2_DATA, 0xFF);
    kprintf("[pic] remapeado a 32..47, habilitados irq0 e irq1\n");
}

// Enmascara las 16 líneas: desde aquí las interrupciones van por el IOAPIC y el LAPIC.
fn pic_disable() { outb(PIC1_DATA, 0xFF); outb(PIC2_DATA, 0xFF); }

fn pic_eoi(irq: u8) {
    if irq >= 8 { outb(PIC2_CMD, PIC_EOI); }
    outb(PIC1_CMD, PIC_EOI);
}

fn pic_set_mask(irq: u8, masked: bool) {
    var port: u16 = PIC1_DATA;
    var line: u8 = irq;
    if irq >= 8 { port = PIC2_DATA; line = irq - 8; }
    var v = inb(port);
    if masked { v = v | ((1 as u8) << line); } else { v = v & ~((1 as u8) << line); }
    outb(port, v);
}
