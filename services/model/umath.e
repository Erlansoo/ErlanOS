// Operaciones a nivel de bits, raíz cuadrada, medias precisiones y lecturas sin alinear para
// el código numérico del runtime del modelo. Portable: el mismo módulo corre dentro de
// ErlanOS y en las pruebas de host. Sustituye a erlan/runtime/umath.h (C) desde el 26/10/2026.

// Bits <-> f32 por registro XMM (sin aliasing de punteros).
fn f32_from_bits(b: u32) -> f32 {
    var f: f32 = 0.0;
    unsafe { asm("movd %1, %0" : "=x"(f) : "r"(b)); }
    return f;
}
fn f32_bits(f: f32) -> u32 {
    var b: u32 = 0;
    unsafe { asm("movd %1, %0" : "=r"(b) : "x"(f)); }
    return b;
}

// AVX2 sólo se puede llamar cuando la CPU Y el sistema conservan el estado XMM/YMM. El
// predicado puro también lo ejercitan las pruebas de host quitando cada prerrequisito.
fn math_avx2_state_ok(max_leaf: u32, leaf1_ecx: u32, leaf7_ebx: u32, xcr0: u64) -> bool {
    let required: u32 = (1 << 26) | (1 << 27) | (1 << 28);
    return max_leaf >= 7 && (leaf1_ecx & required) == required &&
           (leaf7_ebx & (1 << 5)) != 0 && (xcr0 & 6) == 6;
}

// Las pruebas de host piden la ruta portable con math_force_portable(true) (antes era el
// macro ERLAN_MATMUL_FORCE_PORTABLE de C).
var portable_forced: bool = false;
fn math_force_portable(on: bool) { portable_forced = on; }

fn cpuid_regs(leaf: u32, sub: u32, out: &mut [4]u32) {
    var a: u32 = 0; var b: u32 = 0; var c: u32 = 0; var d: u32 = 0;
    unsafe { asm("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(leaf), "c"(sub)); }
    out[0] = a; out[1] = b; out[2] = c; out[3] = d;
}

fn math_cpu_avx2() -> bool {
    if portable_forced { return false; }
    var r: [4]u32;
    cpuid_regs(0, 0, &mut r);
    let maximum = r[0];
    if maximum < 7 { return false; }
    cpuid_regs(1, 0, &mut r);
    let features = r[2];
    let required: u32 = (1 << 26) | (1 << 27) | (1 << 28);
    if (features & required) != required { return false; }
    cpuid_regs(7, 0, &mut r);
    let extended = r[1];
    if (extended & (1 << 5)) == 0 { return false; }
    // XGETBV es ilegal antes de las comprobaciones XSAVE/OSXSAVE de arriba.
    var lo: u32 = 0; var hi: u32 = 0;
    let zero: u32 = 0;
    unsafe { asm("xgetbv" : "=a"(lo), "=d"(hi) : "c"(zero)); }
    return math_avx2_state_ok(maximum, features, extended, ((hi as u64) << 32) | (lo as u64));
}

// IEEE half -> float (normales, subnormales, inf/nan).
fn f16_to_f32(h: u16) -> f32 {
    let sign = ((h & 0x8000) as u32) << 16;
    let exp = ((h >> 10) & 0x1F) as u32;
    var mant = (h & 0x3FF) as u32;
    var bits: u32 = 0;
    if exp == 0 {
        if mant == 0 { bits = sign; }
        else {
            // subnormal: normalizar
            var e: i32 = -1;
            loop { e += 1; mant = mant << 1; if (mant & 0x400) != 0 { break; } }
            mant = mant & 0x3FF;
            bits = sign | (((127 - 15 - e) as u32) << 23) | (mant << 13);
        }
    } else if exp == 31 {
        bits = sign | 0x7F800000 | (mant << 13);
    } else {
        bits = sign | ((exp + 112) << 23) | (mant << 13);
    }
    return f32_from_bits(bits);
}

fn f32_sqrt(x: f32) -> f32 {
    var r: f32 = 0.0;
    unsafe { asm("sqrtss %1, %0" : "=x"(r) : "x"(x)); }
    return r;
}

// Lecturas little-endian sin alineación desde una dirección.
fn ld_u16(a: u64) -> u16 {
    unsafe { let p = a as ptr<u8>; return (p[0] as u16) | ((p[1] as u16) << 8); }
}
fn ld_u32(a: u64) -> u32 {
    unsafe { let p = a as ptr<u8>; return (p[0] as u32) | ((p[1] as u32) << 8) | ((p[2] as u32) << 16) | ((p[3] as u32) << 24); }
}
fn ld_u64(a: u64) -> u64 { return (ld_u32(a) as u64) | ((ld_u32(a + 4) as u64) << 32); }
fn ld_f32(a: u64) -> f32 { return f32_from_bits(ld_u32(a)); }
