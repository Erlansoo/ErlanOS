// Imagen de diagnóstico G0: sólo inventario PCI y reporte en pantalla. Sin servicios,
// sin asignador ni DMA. Es el kmain de build/kernel-probe.elf (make hardware-image).
// Sustituye al bloque ERLANOS_HARDWARE_PROBE de main.c desde el 26/10/2026.
import boot;
import serial;
import cpu;
import gdt;
import idt;
import pci;
import hardware;

fn kmain() {
    boot_begin("ErlanOS L1 (M4) — kernel boot");
    cpu_cli();
    gdt_init();
    idt_init();
    boot_console();
    pci_init();
    hardware_report();
    kprintf("[hardware] probe: ready\n");
    loop { cpu_hlt(); }
}
