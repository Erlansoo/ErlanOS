// L1 — habilitación de x87/SSE/AVX. El kernel se compila sin SSE, pero el model
// runtime (L2) y los procesos necesitan vectores. El guardado de estado XSAVE por
// proceso se implementa en M2 con el scheduler.

extern c "cpu.h" {
    fn cpu_read_cr0() -> u64;
    fn cpu_write_cr0(v: u64);
    fn cpu_read_cr4() -> u64;
    fn cpu_write_cr4(v: u64);
    fn cpu_xgetbv(i: u32) -> u64;
    fn cpu_xsetbv(i: u32, v: u64);
    fn cpu_cpuid(leaf: u32, sub: u32, reg: u32) -> u32;
}
extern c "serial.h" { fn kprintf(fmt: str, ...); }

const CR0_MP: u64 = 1 << 1;
const CR0_EM: u64 = 1 << 2;
const CR0_TS: u64 = 1 << 3;
const CR4_OSFXSR: u64 = 1 << 9;
const CR4_OSXMMEXCPT: u64 = 1 << 10;
const CR4_OSXSAVE: u64 = 1 << 18;

var has_avx: bool = false;
var has_avx2: bool = false;
var has_avx512: bool = false;
var xsave_size: u32 = 512;

fn cpuid_bit(leaf: u32, sub: u32, reg: u32, bit: u32) -> bool {
    return (cpu_cpuid(leaf, sub, reg) & ((1 as u32) << bit)) != 0;
}

fn fpu_init() {
    var cr0 = cpu_read_cr0();
    cr0 = (cr0 & ~CR0_EM & ~CR0_TS) | CR0_MP;
    cpu_write_cr0(cr0);

    var cr4 = cpu_read_cr4() | CR4_OSFXSR | CR4_OSXMMEXCPT;
    cpu_write_cr4(cr4);

    let sse2 = cpuid_bit(1, 0, 3, 26);
    let fma = cpuid_bit(1, 0, 2, 12);
    let xsave = cpuid_bit(1, 0, 2, 26);
    let avx = cpuid_bit(1, 0, 2, 28);
    let avx2 = cpuid_bit(7, 0, 1, 5);
    let avx512f = cpuid_bit(7, 0, 1, 16);

    if xsave {
        cr4 = cr4 | CR4_OSXSAVE;
        cpu_write_cr4(cr4);
        var xcr0 = cpu_xgetbv(0) | 0x3;           // x87 + SSE
        if avx { xcr0 = xcr0 | 0x4; has_avx = true; has_avx2 = avx2; }
        if avx512f { xcr0 = xcr0 | 0xE0; has_avx512 = true; }
        cpu_xsetbv(0, xcr0);
        xsave_size = cpu_cpuid(0xD, 0, 1);         // tamaño del área XSAVE con los bits activos
    }

    kprintf("[fpu] sse2=%d fma=%d avx=%d avx2=%d avx512f=%d xsave=%d area=%d bytes\n",
            sse2 as u64, fma as u64, has_avx as u64, has_avx2 as u64, has_avx512 as u64,
            xsave as u64, xsave_size as u64);
}

fn fpu_has_avx() -> bool { return has_avx; }
fn fpu_has_avx2() -> bool { return has_avx2; }
fn fpu_xsave_size() -> u32 { return xsave_size; }
