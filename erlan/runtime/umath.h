/* Operaciones a nivel de bits y raíz cuadrada para el código numérico Erlan.
 * Portable: sirve en el kernel, en servicios y en pruebas de host. */
#ifndef ERLAN_UMATH_H
#define ERLAN_UMATH_H
#include "erlan_rt.h"

static inline f32 f32_from_bits(u32 b) { union { u32 u; f32 f; } v; v.u = b; return v.f; }
static inline u32 f32_bits(f32 f) { union { u32 u; f32 f; } v; v.f = f; return v.u; }

/* AVX2 is callable only when CPU AND OS preserve the XMM/YMM state. The pure
 * predicate is also exercised by host tests with every prerequisite removed. */
static inline bool math_avx2_state_ok(u32 max_leaf, u32 leaf1_ecx, u32 leaf7_ebx, u64 xcr0) {
    const u32 required = (1u << 26) | (1u << 27) | (1u << 28);
    return max_leaf >= 7 && (leaf1_ecx & required) == required &&
           (leaf7_ebx & (1u << 5)) != 0 && (xcr0 & 6) == 6;
}
static inline bool math_cpu_avx2(void) {
#if defined(ERLAN_MATMUL_FORCE_PORTABLE) || (!defined(__x86_64__) && !defined(__i386__))
    return false;
#else
    u32 a, b, c, d;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0), "c"(0));
    const u32 maximum = a;
    if (maximum < 7) return false;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(1), "c"(0));
    const u32 features = c, required = (1u << 26) | (1u << 27) | (1u << 28);
    if ((features & required) != required) return false;
    __asm__ volatile ("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(7), "c"(0));
    const u32 extended = b;
    if (!(extended & (1u << 5))) return false;
    /* XGETBV itself is illegal before the XSAVE/OSXSAVE checks above. */
    __asm__ volatile ("xgetbv" : "=a"(a), "=d"(d) : "c"(0));
    return math_avx2_state_ok(maximum, features, extended, ((u64)d << 32) | a);
#endif
}

/* IEEE half -> float (normales, subnormales, inf/nan). */
static inline f32 f16_to_f32(u16 h) {
    u32 sign = (u32)(h & 0x8000) << 16;
    u32 exp = (h >> 10) & 0x1F;
    u32 mant = h & 0x3FF;
    u32 bits;
    if (exp == 0) {
        if (mant == 0) bits = sign;
        else {
            /* subnormal: normalizar */
            int e = -1;
            do { e++; mant <<= 1; } while ((mant & 0x400) == 0);
            mant &= 0x3FF;
            bits = sign | ((u32)(127 - 15 - e) << 23) | (mant << 13);
        }
    } else if (exp == 31) {
        bits = sign | 0x7F800000 | (mant << 13);
    } else {
        bits = sign | ((exp + 112) << 23) | (mant << 13);
    }
    return f32_from_bits(bits);
}

#if defined(__x86_64__) || defined(__i386__)
static inline f32 f32_sqrt(f32 x) { f32 r; __asm__ ("sqrtss %1, %0" : "=x"(r) : "x"(x)); return r; }
#else
static inline f32 f32_sqrt(f32 x) { return __builtin_sqrtf(x); }
#endif

/* Lecturas little-endian sin alineación desde una dirección. */
static inline u16 ld_u16(u64 a) { const u8 *p = (const u8 *)(uintptr_t)a; return (u16)(p[0] | (p[1] << 8)); }
static inline u32 ld_u32(u64 a) { const u8 *p = (const u8 *)(uintptr_t)a; return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24); }
static inline u64 ld_u64(u64 a) { return (u64)ld_u32(a) | ((u64)ld_u32(a + 4) << 32); }
static inline f32 ld_f32(u64 a) { return f32_from_bits(ld_u32(a)); }

#endif
