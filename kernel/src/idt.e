// L1 — Interrupt Descriptor Table y despacho de excepciones, IRQs y syscalls.
// Los stubs en ensamblador (isr_stubs.c) guardan los registros y llaman a isr_dispatch.

import cpu;
import isr_stubs;
import serial;
import panic;
import pic;
import pit;
import keyboard;
import pointer;
import sched;
import syscall;
import apic;
import ahci;
import nvme;
import i8042;

struct IdtEntry {
    off_lo: u16;
    sel: u16;
    ist: u8;
    attr: u8;
    off_mid: u16;
    off_hi: u32;
    zero: u32;
}

// Layout exacto de lo que apila isr_common + el CPU (ver isr_stubs.c).
struct IntFrame {
    r15: u64; r14: u64; r13: u64; r12: u64; r11: u64; r10: u64; r9: u64; r8: u64;
    rbp: u64; rdi: u64; rsi: u64; rdx: u64; rcx: u64; rbx: u64; rax: u64;
    vector: u64;
    err: u64;
    rip: u64;
    cs: u64;
    rflags: u64;
    rsp: u64;
    ss: u64;
}

const IDT_GATE_INT: u8 = 0x8E;   // presente, DPL0, interrupt gate (IF=0 al entrar)
const IDT_GATE_TRAP: u8 = 0x8F;  // trap gate (IF se conserva)
const IDT_GATE_USER: u8 = 0xEE;  // interrupt gate invocable desde Ring 3 (DPL3)
const IRQ_BASE: u64 = 32;
const SYSCALL_VECTOR: u64 = 0x80;

var idt: [256]IdtEntry;
var exceptions_handled: u64 = 0;

fn idt_set(v: u64, handler: u64, attr: u8) {
    idt[v].off_lo = (handler & 0xFFFF) as u16;
    idt[v].sel = 0x08;
    idt[v].ist = 0;
    idt[v].attr = attr;
    idt[v].off_mid = ((handler >> 16) & 0xFFFF) as u16;
    idt[v].off_hi = ((handler >> 32) & 0xFFFFFFFF) as u32;
    idt[v].zero = 0;
}

fn idt_init() {
    for v in 0..256 {
        idt_set(v, isr_stub_addr(v), IDT_GATE_INT);
    }
    idt_set(3, isr_stub_addr(3), IDT_GATE_TRAP);                       // #BP como trap
    idt_set(SYSCALL_VECTOR, isr_stub_addr(SYSCALL_VECTOR), IDT_GATE_USER);
    unsafe { cpu_lidt(&idt[0] as u64, 256 * 16 - 1); }
    kprintf("[idt] 256 vectores instalados, syscall en int 0x80\n");
}

fn exception_name(v: u64) -> str {
    match v {
        0  => { return "#DE division por cero"; }
        1  => { return "#DB debug"; }
        2  => { return "NMI"; }
        3  => { return "#BP breakpoint"; }
        4  => { return "#OF overflow"; }
        5  => { return "#BR bound range"; }
        6  => { return "#UD opcode invalido"; }
        7  => { return "#NM dispositivo no disponible (FPU)"; }
        8  => { return "#DF double fault"; }
        10 => { return "#TS TSS invalido"; }
        11 => { return "#NP segmento no presente"; }
        12 => { return "#SS stack fault"; }
        13 => { return "#GP proteccion general"; }
        14 => { return "#PF page fault"; }
        16 => { return "#MF x87 FP"; }
        17 => { return "#AC alignment check"; }
        18 => { return "#MC machine check"; }
        19 => { return "#XM SIMD FP"; }
        _  => { return "excepcion"; }
    }
    return "excepcion";
}

fn exception_mode(cs: u64) -> str {
    if (cs & 3) == 3 { return "Ring 3 (usuario)"; }
    return "Ring 0 (kernel)";
}

// Llamado desde isr_common con el frame en la pila.
fn isr_dispatch(f: ptr<IntFrame>) {
    if f.vector < IRQ_BASE {
        exceptions_handled += 1;
        if f.vector == 3 {
            kprintf("[int] #BP breakpoint en rip=%x: manejado, continuando\n", f.rip);
            return;
        }
        kprintf("\n[int] EXCEPCION %d (%s) err=%x en %s\n",
                f.vector, exception_name(f.vector), f.err, exception_mode(f.cs));
        kprintf("  rip=%x cs=%x rflags=%x rsp=%x ss=%x\n", f.rip, f.cs, f.rflags, f.rsp, f.ss);
        kprintf("  rax=%x rbx=%x rcx=%x rdx=%x\n", f.rax, f.rbx, f.rcx, f.rdx);
        kprintf("  rsi=%x rdi=%x rbp=%x\n", f.rsi, f.rdi, f.rbp);
        if f.vector == 14 { kprintf("  cr2=%x\n", cpu_read_cr2()); }
        kpanic("excepcion de CPU no manejada");
    }
    if f.vector < IRQ_BASE + 16 {
        let irq = f.vector - IRQ_BASE;
        // EOI primero: el handler puede cambiar de hilo y no volver hasta mucho después.
        // En modo IOAPIC la interrupción vino por el LAPIC; en modo PIC, por el 8259.
        if apic_ioapic_mode() { apic_eoi(); } else { pic_eoi(irq as u8); }
        match irq {
            0 => { pit_tick(); sched_tick(); }
            1 | 12 => { i8042_irq(); }
            7 | 15 => { }                     // espurias del 8259 al enmascararlo
            _ => { kprintf("[int] irq %d sin handler\n", irq); }
        }
        return;
    }
    if f.vector == SYSCALL_VECTOR {
        f.rax = syscall_dispatch(f.rax, f.rdi, f.rsi, f.rdx);
        return;
    }
    if f.vector == 48 {
        // Tick del sistema por el temporizador del LAPIC (apic_timer_start enmascaró la IRQ0).
        apic_eoi(); apic_timer_irq(); pit_tick(); sched_tick();
        return;
    }
    if f.vector == 49 { apic_eoi(); ahci_irq(); return; }
    if f.vector == 50 { apic_eoi(); nvme_irq(); return; }
    if f.vector == 255 { return; }     // espurio del LAPIC: sin EOI
    kprintf("[int] vector %d ignorado\n", f.vector);
}
