/* Native x86 regression: zero XSAVE leaves MXCSR unmasked; initialized state
 * must survive an inexact SSE division. Original host FP state is restored. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "cpu.h"

int main(void) {
    _Alignas(64) u8 fresh[4096], saved[4096];
    memset(fresh, 0, sizeof(fresh)); memset(saved, 0, sizeof(saved));
    cpu_init_xstate((u64)(uintptr_t)fresh);
    assert(fresh[0] == 0x7f && fresh[1] == 3);
    assert(fresh[24] == 0x80 && fresh[25] == 0x1f && fresh[26] == 0 && fresh[27] == 0);
    for (size_t i = 512; i < 576; ++i) assert(fresh[i] == 0); // no fabricated XSTATE_BV

    u32 features = cpu_cpuid(1, 0, 2);
    if ((features & ((1u << 26) | (1u << 27))) != ((1u << 26) | (1u << 27)) ||
        (cpu_xgetbv(0) & 3) != 3) {
        puts("XSTATE initialization PASS; native XRSTOR skipped (host XSAVE unavailable)");
        return 0;
    }
    u32 unseeded, initial, after;
    const u32 one = 0x3f800000, three = 0x40400000;
    memset(fresh, 0, sizeof(fresh));
    // Only x87/SSE are requested; no dependence on host AVX/AMX save-area size.
    __asm__ volatile("xsave64 (%0)" : : "r"(saved), "a"(3u), "d"(0u) : "memory");
    __asm__ volatile("xrstor64 (%1)\n\tstmxcsr %0\n\txrstor64 (%2)"
                     : "=m"(unseeded) : "r"(fresh), "r"(saved), "a"(3u), "d"(0u)
                     : "memory", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5",
                       "xmm6", "xmm7", "xmm8", "xmm9", "xmm10", "xmm11", "xmm12", "xmm13", "xmm14", "xmm15");
    cpu_init_xstate((u64)(uintptr_t)fresh);
    __asm__ volatile("xrstor64 (%2)\n\tstmxcsr %0\n\tmovss %3, %%xmm0\n\tdivss %4, %%xmm0\n\tstmxcsr %1\n\txrstor64 (%5)"
                     : "=m"(initial), "=m"(after) : "r"(fresh), "m"(one), "m"(three), "r"(saved), "a"(3u), "d"(0u)
                     : "memory", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5",
                       "xmm6", "xmm7", "xmm8", "xmm9", "xmm10", "xmm11", "xmm12", "xmm13", "xmm14", "xmm15");
    assert(unseeded == 0); // reproduces the scheduler's previous initial state
    assert(initial == 0x1f80 && (after & 0x1f80) == 0x1f80 && (after & 0x20));
    printf("XSTATE PASS native unseeded=0x%x initialized=0x%x after-inexact=0x%x\n", unseeded, initial, after);
    return 0;
}
