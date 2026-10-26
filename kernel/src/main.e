// ErlanOS L1 — entrada del kernel normal (M4), en Erlan. Nada de IA aquí: el modelo corre en
// un servicio de L2 (services/model). Las capabilities se conceden aquí, explícitamente,
// por proceso. Sustituye a main.c desde el 26/10/2026.
import boot;
import limine;
import serial;
import panic;
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
import storage;
import pointer;
import acpi;
import apic;
import i8042;

// Tipos de capability y derechos (deben coincidir con sched.e / syscall.e).
const CAP_LOG: u32 = 1;
const CAP_TIME: u32 = 2;
const CAP_ENDPOINT: u32 = 3;
const CAP_CONSOLE: u32 = 4;
const CAP_INPUT: u32 = 5;
const CAP_MODULE: u32 = 6;
const CAP_MEMORY: u32 = 7;
const CAP_SYSTEM: u32 = 8;
const CAP_STORAGE: u32 = 10;
const RIGHT_SEND: u32 = 1;
const RIGHT_RECV: u32 = 2;

// "/boot/init.bin" -> nombre "init" y extensión "bin" (buffers estáticos por módulo).
var module_names: [8][32]u8;
var module_exts: [8][8]u8;

fn module_short_name(i: u64, path: str) -> str {
    unsafe {
        var base: u64 = 0;
        var p: u64 = 0;
        while path[p] != 0 { if path[p] == '/' { base = p + 1; } p += 1; }
        var dot: u64 = 0;
        var has_dot = false;
        p = base;
        while path[p] != 0 { if path[p] == '.' { dot = p; has_dot = true; } p += 1; }
        var n: u64 = 0;
        while path[base + n] != 0 && !(has_dot && base + n == dot) && n < 31 {
            module_names[i][n] = path[base + n]; n += 1;
        }
        module_names[i][n] = 0;
        var e: u64 = 0;
        if has_dot {
            var q = dot + 1;
            while path[q] != 0 && e < 7 { module_exts[i][e] = path[q]; e += 1; q += 1; }
        }
        module_exts[i][e] = 0;
        return &module_names[i][0] as str;
    }
}

fn str_eq(a: str, b: str) -> bool {
    unsafe {
        var i: u64 = 0;
        while a[i] != 0 && a[i] == b[i] { i += 1; }
        return a[i] == b[i];
    }
}

fn kmain() {
    boot_begin("ErlanOS L1 (M4) — kernel boot");

    // --- M1: base de máquina ---
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

    // --- M2: memoria virtual, hilos, IPC ---
    vmm_init();
    // ACPI (mapea sus regiones en las tablas del kernel y lee RSDP, XSDT, MADT, MCFG, HPET) y
    // APIC: el tick del sistema pasa del PIT al temporizador del LAPIC.
    acpi_init();
    i8042_init();                 // tras ACPI: la FADT dice si hay 8042
    if apic_init() { apic_timer_start(100); apic_switch_to_ioapic(); }
    sched_init();
    ipc_init();
    proc_init();

    // Pantalla: puntos + consola de texto
    boot_console();
    storage_init();
    pointer_init();
    desktop_end();

    // Módulos de Limine: .bin = servicio de usuario (L2); .gguf = modelo (datos).
    let ep = ipc_create();
    let ai_requests = ipc_create();
    let ai_replies = ipc_create();
    var data_idx: u64 = 0;
    let count = boot_module_count();
    kprintf("modulos: %d\n", count);
    for i in 0..count {
        if i >= 8 { break; }
        let m = boot_module(i);
        var path: str = "";
        unsafe { path = m.path as str; }
        let name = module_short_name(i, path);
        kprintf("  %s  (%d bytes)\n", path, m.size);
        var ext: str = "";
        unsafe { ext = &module_exts[i][0] as str; }
        if str_eq(ext, "gguf") {
            proc_register_module(data_idx, name, m.address, m.size);
            data_idx += 1;
            continue;
        }
        if !str_eq(ext, "bin") { continue; }
        let id = proc_spawn(name, m.address, m.size);
        if id == 0 { kpanic("proc: imagen de servicio invalida o sin memoria"); }
        sched_grant(id, 0, CAP_LOG, 0, 0);
        sched_grant(id, 1, CAP_TIME, 0, 0);
        if str_eq(name, "init") {
            sched_grant(id, 2, CAP_ENDPOINT, RIGHT_SEND, ep);
            sched_grant(id, 7, CAP_SYSTEM, 1, 0);   // sólo lectura, probado en init
        }
        if str_eq(name, "echo") { sched_grant(id, 2, CAP_ENDPOINT, RIGHT_RECV, ep); }
        if str_eq(name, "model") {
            sched_grant(id, 3, CAP_CONSOLE, 0, 0);
            sched_grant(id, 4, CAP_INPUT, 0, 0);
            sched_grant(id, 5, CAP_MODULE, 0, 0);
            sched_grant(id, 6, CAP_MEMORY, 0, 0);
            sched_grant(id, 8, CAP_ENDPOINT, RIGHT_SEND, ai_requests);
            sched_grant(id, 9, CAP_ENDPOINT, RIGHT_RECV, ai_replies);
        }
        if str_eq(name, "system") {
            sched_grant(id, 12, CAP_STORAGE, 3, 0);
            sched_grant(id, 3, CAP_CONSOLE, 1, 0);   // presentación estructurada
            sched_grant(id, 7, CAP_SYSTEM, 3, 0);    // lectura + cambiar modo
            sched_grant(id, 8, CAP_ENDPOINT, RIGHT_RECV, ai_requests);
            sched_grant(id, 9, CAP_ENDPOINT, RIGHT_SEND, ai_replies);
        }
    }

    kprintf("estado: idle  (• • •)  cediendo el CPU a los servicios\n");
    i8042_drain();                // un byte que ya espera no daría flanco en el IOAPIC
    sched_start();
}
