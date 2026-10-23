/* Shims de CPU para el código Erlan de L1. Todo lo que exige ensamblador vive aquí,
 * como funciones inline diminutas. La lógica va en los módulos .e */
#ifndef ERLANOS_CPU_H
#define ERLANOS_CPU_H

#include "erlan_rt.h"

static inline void outb(u16 port, u8 v) { __asm__ volatile ("outb %0, %1" : : "a"(v), "Nd"(port) : "memory"); }
static inline u8 inb(u16 port) { u8 r; __asm__ volatile ("inb %1, %0" : "=a"(r) : "Nd"(port) : "memory"); return r; }
static inline void io_wait(void) { outb(0x80, 0); }

static inline void cpu_cli(void) { __asm__ volatile ("cli" ::: "memory"); }
static inline void cpu_sti(void) { __asm__ volatile ("sti" ::: "memory"); }
static inline void cpu_hlt(void) { __asm__ volatile ("hlt" ::: "memory"); }
static inline void cpu_int3(void) { __asm__ volatile ("int3"); }

struct __attribute__((packed)) cpu_table_ptr { u16 limit; u64 base; };

static inline void cpu_lgdt(u64 base, u16 limit) {
    struct cpu_table_ptr p = { limit, base };
    __asm__ volatile ("lgdt %0" : : "m"(p) : "memory");
}

static inline void cpu_lidt(u64 base, u16 limit) {
    struct cpu_table_ptr p = { limit, base };
    __asm__ volatile ("lidt %0" : : "m"(p) : "memory");
}

/* Recarga CS con un retorno lejano y los selectores de datos. */
static inline void cpu_reload_segments(u16 code, u16 data) {
    __asm__ volatile (
        "pushq %q0\n"
        "leaq 1f(%%rip), %%rax\n"
        "pushq %%rax\n"
        "lretq\n"
        "1:\n"
        "movw %w1, %%ds\n"
        "movw %w1, %%es\n"
        "movw %w1, %%fs\n"
        "movw %w1, %%gs\n"
        "movw %w1, %%ss\n"
        : : "r"((u64)code), "r"(data) : "rax", "memory");
}

static inline u64 cpu_read_cr0(void) { u64 v; __asm__ volatile ("movq %%cr0, %0" : "=r"(v)); return v; }
static inline void cpu_write_cr0(u64 v) { __asm__ volatile ("movq %0, %%cr0" : : "r"(v) : "memory"); }
static inline u64 cpu_read_cr2(void) { u64 v; __asm__ volatile ("movq %%cr2, %0" : "=r"(v)); return v; }
static inline u64 cpu_read_cr3(void) { u64 v; __asm__ volatile ("movq %%cr3, %0" : "=r"(v)); return v; }
static inline u64 cpu_read_cr4(void) { u64 v; __asm__ volatile ("movq %%cr4, %0" : "=r"(v)); return v; }
static inline void cpu_write_cr4(u64 v) { __asm__ volatile ("movq %0, %%cr4" : : "r"(v) : "memory"); }

static inline void cpu_write_cr3(u64 v) { __asm__ volatile ("movq %0, %%cr3" : : "r"(v) : "memory"); }
static inline void cpu_invlpg(u64 va) { __asm__ volatile ("invlpg (%0)" : : "r"(va) : "memory"); }
static inline void cpu_ltr(u16 sel) { __asm__ volatile ("ltr %0" : : "r"(sel) : "memory"); }

/* Sembrar defaults en un área XSAVE previamente puesta a cero. XRSTOR carga
 * MXCSR incluso cuando XSTATE_BV marca SSE como estado inicial. MXCSR=0 deja
 * todas las excepciones SIMD sin máscara y falla en la primera división inexacta.
 * x87 FCW=0x037f; SSE MXCSR=0x1f80 (round-to-nearest, excepciones enmascaradas). */
static inline void cpu_init_xstate(u64 area) {
    u8 *p = (u8 *)(uintptr_t)area;
    p[0] = 0x7f; p[1] = 0x03;
    p[24] = 0x80; p[25] = 0x1f; p[26] = 0; p[27] = 0;
}

/* Área XSAVE alineada a 64 bytes; máscara completa. */
static inline void cpu_xsave(u64 area) {
    __asm__ volatile ("xsave64 (%0)" : : "r"(area), "a"(0xffffffffu), "d"(0xffffffffu) : "memory");
}
static inline void cpu_xrstor(u64 area) {
    __asm__ volatile ("xrstor64 (%0)" : : "r"(area), "a"(0xffffffffu), "d"(0xffffffffu) : "memory");
}

static inline u64 cpu_xgetbv(u32 i) {
    u32 lo, hi; __asm__ volatile ("xgetbv" : "=a"(lo), "=d"(hi) : "c"(i)); return ((u64)hi << 32) | lo;
}
static inline void cpu_xsetbv(u32 i, u64 v) {
    __asm__ volatile ("xsetbv" : : "c"(i), "a"((u32)v), "d"((u32)(v >> 32)));
}

/* reg: 0=eax 1=ebx 2=ecx 3=edx */
static inline u32 cpu_cpuid(u32 leaf, u32 sub, u32 reg) {
    u32 a, b, c, d;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(leaf), "c"(sub));
    switch (reg) { case 0: return a; case 1: return b; case 2: return c; default: return d; }
}

static inline u64 cpu_rdtsc(void) {
    u32 lo, hi; __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi)); return ((u64)hi << 32) | lo;
}

#endif
