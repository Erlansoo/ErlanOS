// L1 — hilos y scheduler determinista (round-robin por quantum del PIT).
// En M2 proceso == hilo. Clases de prioridad (REALTIME, INTERACTIVE, AI_*) llegan en M3+.

import cpu;
import switch;
import pmm;
import kmem;
import vmm;
import gdt;
import pit;
import serial;
import panic;

const MAX_THREADS: u64 = 32;
const MAX_CAPS: u64 = 16;
const KSTACK_PAGES: u64 = 4;
const QUANTUM_TICKS: u64 = 5;

const T_FREE: u32 = 0;
const T_READY: u32 = 1;
const T_RUNNING: u32 = 2;
const T_BLOCKED: u32 = 3;
const T_SLEEPING: u32 = 4;
const T_DEAD: u32 = 5;

// Capability: autoridad sobre un objeto del kernel. Sin cap no hay efecto.
const CAP_NONE: u32 = 0;
const CAP_LOG: u32 = 1;        // escribir en la consola del sistema
const CAP_TIME: u32 = 2;       // leer el reloj
const CAP_ENDPOINT: u32 = 3;   // enviar/recibir por un endpoint IPC

const RIGHT_SEND: u32 = 1;
const RIGHT_RECV: u32 = 2;

struct Cap {
    kind: u32;
    rights: u32;
    obj: u64;
}

struct Thread {
    id: u64;
    state: u32;
    user: bool;
    name: str;
    kstack_top: u64;
    rsp: u64;           // rsp de kernel guardado por switch_context
    cr3: u64;
    xsave: u64;         // área XSAVE (4 KiB, alineada a página)
    wake_at: u64;       // tick de despertar si SLEEPING
    wait_obj: u64;      // objeto por el que espera si BLOCKED
    quantum: u64;
    caps: [16]Cap;
}

var threads: [32]Thread;
var current: u64 = 0;
var thread_count: u64 = 0;
var switches: u64 = 0;

fn sched_init() {
    for i in 0..MAX_THREADS { threads[i].state = T_FREE; }
    // El hilo 0 es el contexto de arranque (kmain): se vuelve el hilo idle.
    threads[0].id = 0;
    threads[0].state = T_RUNNING;
    threads[0].user = false;
    threads[0].name = "idle";
    threads[0].cr3 = vmm_kernel_cr3();
    threads[0].xsave = pmm_phys_to_virt(pmm_alloc_page());
    kmemset(threads[0].xsave, 0, 4096);
    cpu_init_xstate(threads[0].xsave);
    threads[0].quantum = QUANTUM_TICKS;
    current = 0;
    thread_count = 1;
    kprintf("[sched] listo, quantum %d ticks\n", QUANTUM_TICKS);
}

fn sched_current() -> ptr<Thread> { return &threads[current]; }
fn sched_current_id() -> u64 { return current; }
fn sched_thread(id: u64) -> ptr<Thread> { return &threads[id]; }

// Crea un hilo listo para ejecutar `rip` con la pila `rsp` en el espacio `cr3`.
// El primer cambio de contexto hace `ret` al trampolín, que hace `iretq` con este frame.
fn sched_create(name: str, rip: u64, rsp: u64, cr3: u64, user: bool) -> u64 {
    var id: u64 = MAX_THREADS;
    for i in 1..MAX_THREADS {
        if threads[i].state == T_FREE && id == MAX_THREADS { id = i; }
    }
    if id == MAX_THREADS { kpanic("sched: sin hilos libres"); }

    let t = &mut threads[id];
    t.id = id;
    t.user = user;
    t.name = name;
    t.cr3 = cr3;
    t.wake_at = 0;
    t.wait_obj = 0;
    t.quantum = QUANTUM_TICKS;
    for c in 0..MAX_CAPS { t.caps[c].kind = CAP_NONE; t.caps[c].rights = 0; t.caps[c].obj = 0; }

    let kphys = pmm_alloc_pages(KSTACK_PAGES);
    t.kstack_top = pmm_phys_to_virt(kphys) + KSTACK_PAGES * 4096;
    t.xsave = pmm_phys_to_virt(pmm_alloc_page());
    kmemset(t.xsave, 0, 4096);
    cpu_init_xstate(t.xsave);

    var cs: u64 = 0x08;
    var ss: u64 = 0x10;
    if user { cs = gdt_user_code(); ss = gdt_user_data(); }

    // Frame inicial: 6 callee-saved (ceros), retorno al trampolín, frame iretq.
    let sp = t.kstack_top - 12 * 8;
    unsafe {
        let p = sp as ptr<u64>;
        for i in 0..6 { p[i] = 0; }
        p[6] = thread_trampoline_addr();
        p[7] = rip;
        p[8] = cs;
        p[9] = 0x202;         // IF=1
        p[10] = rsp;
        p[11] = ss;
    }
    t.rsp = sp;
    t.state = T_READY;
    thread_count += 1;
    kprintf("[sched] hilo %d '%s' creado (rip=%x cr3=%x user=%d)\n", id, name, rip, cr3, user as u64);
    return id;
}

fn sched_grant(id: u64, slot: u64, kind: u32, rights: u32, obj: u64) {
    if slot >= MAX_CAPS { kpanic("sched: slot de cap invalido"); }
    threads[id].caps[slot].kind = kind;
    threads[id].caps[slot].rights = rights;
    threads[id].caps[slot].obj = obj;
}

// Comprueba que el hilo actual tenga en `slot` una cap del tipo y derechos pedidos.
fn sched_check_cap(slot: u64, kind: u32, rights: u32) -> bool {
    if slot >= MAX_CAPS { return false; }
    let c = &threads[current].caps[slot];
    if c.kind != kind { return false; }
    if (c.rights & rights) != rights { return false; }
    return true;
}

fn sched_cap_obj(slot: u64) -> u64 { return threads[current].caps[slot].obj; }

fn wake_sleepers() {
    let now = pit_ticks();
    for i in 0..MAX_THREADS {
        if threads[i].state == T_SLEEPING && threads[i].wake_at <= now { threads[i].state = T_READY; }
    }
}

// Elige el siguiente hilo READY después del actual (round-robin). 0 = idle si no hay otro.
fn pick_next() -> u64 {
    for k in 1..MAX_THREADS {
        let i = (current + k) % MAX_THREADS;
        if i != 0 && threads[i].state == T_READY { return i; }
    }
    if threads[current].state == T_RUNNING || threads[current].state == T_READY { return current; }
    return 0;
}

// Cambia al hilo `next`. Se llama con interrupciones deshabilitadas (dentro de una ISR).
fn switch_to(next: u64) {
    let prev = current;
    if next == prev { return; }
    if threads[prev].state == T_RUNNING { threads[prev].state = T_READY; }
    threads[next].state = T_RUNNING;
    threads[next].quantum = QUANTUM_TICKS;
    switches += 1;

    cpu_xsave(threads[prev].xsave);
    vmm_switch(threads[next].cr3);
    tss_set_rsp0(threads[next].kstack_top);
    cpu_xrstor(threads[next].xsave);
    current = next;
    switch_context(&threads[prev].rsp, threads[next].rsp);
    // Volvemos aquí cuando alguien cambia de vuelta a `prev`.
}

// Cede el CPU (desde una syscall o el tick). El estado del actual ya fue fijado por el llamante.
fn sched_yield() {
    wake_sleepers();
    switch_to(pick_next());
}

// IRQ0: descuenta quantum y preempta.
fn sched_tick() {
    wake_sleepers();
    if threads[current].quantum > 0 { threads[current].quantum -= 1; }
    if threads[current].quantum == 0 || current == 0 {
        switch_to(pick_next());
    }
}

fn sched_sleep(ticks: u64) {
    threads[current].wake_at = pit_ticks() + ticks;
    threads[current].state = T_SLEEPING;
    sched_yield();
}

fn sched_block(obj: u64) {
    threads[current].wait_obj = obj;
    threads[current].state = T_BLOCKED;
    sched_yield();
}

fn sched_wake(id: u64) {
    if threads[id].state == T_BLOCKED { threads[id].state = T_READY; threads[id].wait_obj = 0; }
}

fn sched_exit(code: u64) {
    kprintf("[sched] hilo %d '%s' termino con codigo %d\n", current, threads[current].name, code);
    threads[current].state = T_DEAD;   // pilas y páginas se reciclan en M3
    thread_count -= 1;
    sched_yield();
    kpanic("sched: un hilo muerto volvio a ejecutar");
}

fn sched_switches() -> u64 { return switches; }
// Incluye idle y todos los hilos vivos (también bloqueados o durmiendo).
fn sched_live_count() -> u64 { return thread_count; }
fn sched_thread_name(id: u64) -> str { return threads[id].name; }
fn sched_current_cr3() -> u64 { return threads[current].cr3; }

// El hilo 0 se convierte en idle: espera interrupciones para siempre.
fn sched_start() {
    kprintf("[sched] %d hilos; arrancando idle\n", thread_count);
    cpu_sti();
    loop { cpu_hlt(); }
}
