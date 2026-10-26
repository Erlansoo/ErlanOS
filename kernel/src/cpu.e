// L1 — instrucciones privilegiadas de la CPU x86-64. Todo lo que exige ensamblador vive
// aquí, como funciones pequeñas con `asm` dentro de `unsafe`; la lógica va en los demás
// módulos. Sustituye a cpu.h y storage_io.h (C) desde el 26/10/2026.

// Puertos de E/S.
fn outb(port: u16, v: u8) { unsafe { asm("outb %0, %1" : : "a"(v), "Nd"(port) : "memory"); } }
fn inb(port: u16) -> u8 { var r: u8 = 0; unsafe { asm("inb %1, %0" : "=a"(r) : "Nd"(port) : "memory"); } return r; }
fn outw(port: u16, v: u16) { unsafe { asm("outw %0, %1" : : "a"(v), "Nd"(port) : "memory"); } }
fn inw(port: u16) -> u16 { var r: u16 = 0; unsafe { asm("inw %1, %0" : "=a"(r) : "Nd"(port) : "memory"); } return r; }
fn outl(port: u16, v: u32) { unsafe { asm("outl %0, %1" : : "a"(v), "Nd"(port) : "memory"); } }
fn inl(port: u16) -> u32 { var r: u32 = 0; unsafe { asm("inl %1, %0" : "=a"(r) : "Nd"(port) : "memory"); } return r; }
fn io_wait() { outb(0x80, 0); }

// Los nombres que usa storage.e (VirtIO legado por puertos).
fn storage_inw(p: u16) -> u16 { return inw(p); }
fn storage_inl(p: u16) -> u32 { return inl(p); }
fn storage_outw(p: u16, v: u16) { outw(p, v); }
fn storage_outl(p: u16, v: u32) { outl(p, v); }

// Interrupciones y parada.
fn cpu_cli() { unsafe { asm("cli" : : : "memory"); } }
fn cpu_sti() { unsafe { asm("sti" : : : "memory"); } }
fn cpu_hlt() { unsafe { asm("hlt" : : : "memory"); } }
fn cpu_int3() { unsafe { asm("int3"); } }

// Descriptor de 10 bytes (límite de 16 bits + base de 64) para lgdt/lidt, armado byte a
// byte: Erlan v0 no tiene structs empaquetados.
fn table_descriptor(out: &mut [10]u8, base: u64, limit: u16) {
    out[0] = (limit & 0xFF) as u8;
    out[1] = (limit >> 8) as u8;
    for i in 0..8 { out[2 + i] = ((base >> (8 * i)) & 0xFF) as u8; }
}
fn cpu_lgdt(base: u64, limit: u16) {
    var d: [10]u8;
    table_descriptor(&mut d, base, limit);
    var p: u64 = 0;
    unsafe { p = &d[0] as u64; asm("lgdt (%0)" : : "r"(p) : "memory"); }
}
fn cpu_lidt(base: u64, limit: u16) {
    var d: [10]u8;
    table_descriptor(&mut d, base, limit);
    var p: u64 = 0;
    unsafe { p = &d[0] as u64; asm("lidt (%0)" : : "r"(p) : "memory"); }
}

// Recarga CS con un retorno lejano y los selectores de datos.
fn cpu_reload_segments(code: u16, data: u16) {
    let code64 = code as u64;
    unsafe {
        asm("pushq %q0\n"
            "leaq 1f(%%rip), %%rax\n"
            "pushq %%rax\n"
            "lretq\n"
            "1:\n"
            "movw %w1, %%ds\n"
            "movw %w1, %%es\n"
            "movw %w1, %%fs\n"
            "movw %w1, %%gs\n"
            "movw %w1, %%ss\n"
            : : "r"(code64), "r"(data) : "rax", "memory");
    }
}

// Registros de control.
fn cpu_read_cr0() -> u64 { var v: u64 = 0; unsafe { asm("movq %%cr0, %0" : "=r"(v)); } return v; }
fn cpu_write_cr0(v: u64) { unsafe { asm("movq %0, %%cr0" : : "r"(v) : "memory"); } }
fn cpu_read_cr2() -> u64 { var v: u64 = 0; unsafe { asm("movq %%cr2, %0" : "=r"(v)); } return v; }
fn cpu_read_cr3() -> u64 { var v: u64 = 0; unsafe { asm("movq %%cr3, %0" : "=r"(v)); } return v; }
fn cpu_read_cr4() -> u64 { var v: u64 = 0; unsafe { asm("movq %%cr4, %0" : "=r"(v)); } return v; }
fn cpu_write_cr4(v: u64) { unsafe { asm("movq %0, %%cr4" : : "r"(v) : "memory"); } }
fn cpu_write_cr3(v: u64) { unsafe { asm("movq %0, %%cr3" : : "r"(v) : "memory"); } }
fn cpu_invlpg(va: u64) { unsafe { asm("invlpg (%0)" : : "r"(va) : "memory"); } }
fn cpu_ltr(sel: u16) { unsafe { asm("ltr %0" : : "r"(sel) : "memory"); } }

// Sembrar defaults en un área XSAVE previamente puesta a cero. XRSTOR carga MXCSR incluso
// cuando XSTATE_BV marca SSE como estado inicial. MXCSR=0 deja todas las excepciones SIMD sin
// máscara y falla en la primera división inexacta. x87 FCW=0x037f; SSE MXCSR=0x1f80.
fn cpu_init_xstate(area: u64) {
    unsafe {
        let p = area as ptr<u8>;
        p[0] = 0x7f; p[1] = 0x03;
        p[24] = 0x80; p[25] = 0x1f; p[26] = 0; p[27] = 0;
    }
}

// Área XSAVE alineada a 64 bytes; máscara completa.
fn cpu_xsave(area: u64) {
    let mask: u32 = 0xffffffff;
    unsafe { asm("xsave64 (%0)" : : "r"(area), "a"(mask), "d"(mask) : "memory"); }
}
fn cpu_xrstor(area: u64) {
    let mask: u32 = 0xffffffff;
    unsafe { asm("xrstor64 (%0)" : : "r"(area), "a"(mask), "d"(mask) : "memory"); }
}
fn cpu_xgetbv(i: u32) -> u64 {
    var lo: u32 = 0; var hi: u32 = 0;
    unsafe { asm("xgetbv" : "=a"(lo), "=d"(hi) : "c"(i)); }
    return ((hi as u64) << 32) | (lo as u64);
}
fn cpu_xsetbv(i: u32, v: u64) {
    let lo = (v & 0xffffffff) as u32;
    let hi = (v >> 32) as u32;
    unsafe { asm("xsetbv" : : "c"(i), "a"(lo), "d"(hi)); }
}

// reg: 0=eax 1=ebx 2=ecx 3=edx
fn cpu_cpuid(leaf: u32, sub: u32, reg: u32) -> u32 {
    var a: u32 = 0; var b: u32 = 0; var c: u32 = 0; var d: u32 = 0;
    unsafe { asm("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(leaf), "c"(sub)); }
    if reg == 0 { return a; }
    if reg == 1 { return b; }
    if reg == 2 { return c; }
    return d;
}

fn cpu_rdtsc() -> u64 {
    var lo: u32 = 0; var hi: u32 = 0;
    unsafe { asm("rdtsc" : "=a"(lo), "=d"(hi)); }
    return ((hi as u64) << 32) | (lo as u64);
}

// Registros específicos del modelo (MSR): IA32_APIC_BASE (0x1B) para el LAPIC.
fn cpu_rdmsr(msr: u32) -> u64 {
    var lo: u32 = 0; var hi: u32 = 0;
    unsafe { asm("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr)); }
    return ((hi as u64) << 32) | (lo as u64);
}
fn cpu_wrmsr(msr: u32, v: u64) {
    let lo = (v & 0xffffffff) as u32;
    let hi = (v >> 32) as u32;
    unsafe { asm("wrmsr" : : "c"(msr), "a"(lo), "d"(hi) : "memory"); }
}
