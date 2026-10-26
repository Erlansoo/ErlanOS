// Imagen de prueba de red (N0/N1): mismo arranque que el kernel normal, sin servicios.
// El driver e1000 (L1) y la pila net/ (Erlan) demuestran DHCP, DNS, TCP y HTTP reales.
// Es el kmain de build/kernel-net.elf (make net-image).
// Sustituye al bloque ERLANOS_NET_PROBE de main.c desde el 26/10/2026.
import boot;
import serial;
import cpu;
import gdt;
import idt;
import pic;
import pit;
import keyboard;
import pmm;
import heap;
import fpu;
import vmm;
import sched;
import ipc;
import proc;
import console;
import pci;
import e1000;
import netmain;
import acpi;
import apic;
import i8042;

fn kmain() {
    boot_begin("ErlanOS L1 (M4) — kernel boot");
    gdt_init();
    idt_init();
    pci_init();
    pic_init();
    pit_init(100);
    keyboard_init();
    pmm_init(boot_regions(), boot_hhdm());
    heap_init();
    heap_selftest();
    fpu_init();
    vmm_init();
    acpi_init();
    i8042_init();
    if apic_init() { apic_timer_start(100); apic_switch_to_ioapic(); }
    sched_init();
    ipc_init();
    proc_init();
    boot_console();
    kprintf("[net] probe: inicio\n");
    if !e1000_init() {
        let msg: str = "Sin NIC e1000 compatible: la prueba de red no puede continuar.\n";
        unsafe { console_write(msg as ptr<u8>, 63); }
        kprintf("[net] FALLO: sin NIC e1000 compatible\n");
        kprintf("[net] probe: ready\n");
        loop { cpu_hlt(); }
    }
    i8042_drain();
    cpu_sti();
    net_probe_run();
    kprintf("[net] probe: ready\n");
    loop { cpu_hlt(); }
}
