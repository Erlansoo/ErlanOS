/* Copias de memoria por dirección (u64) para el código Erlan de L1. */
#ifndef ERLANOS_KMEM_H
#define ERLANOS_KMEM_H
#include "erlan_rt.h"

static inline void kmemcpy(u64 dst, u64 src, u64 n) {
    u8 *d = (u8 *)dst; const u8 *s = (const u8 *)src;
    for (u64 i = 0; i < n; i++) d[i] = s[i];
}
static inline void kmemset(u64 dst, u8 v, u64 n) {
    u8 *d = (u8 *)dst;
    for (u64 i = 0; i < n; i++) d[i] = v;
}
static inline u64 kstrlen(const char *s) { u64 n = 0; while (s[n]) n++; return n; }

#endif
