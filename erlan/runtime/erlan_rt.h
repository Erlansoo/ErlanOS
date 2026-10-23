/* Runtime mínimo de Erlan v0 para código generado (Erlan -> C).
 * Freestanding: no depende de libc. El programa anfitrión debe definir erlan_panic. */
#ifndef ERLAN_RT_H
#define ERLAN_RT_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef int8_t   i8;  typedef int16_t  i16; typedef int32_t i32; typedef int64_t i64;
typedef uint8_t  u8;  typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef float    f32; typedef double   f64;

/* Panic: implementado por el anfitrión (en el kernel imprime por serial y detiene). */
__attribute__((noreturn)) void erlan_panic(const char *msg);

/* Comprobación de índice para slices y arrays: devuelve i o hace panic. */
static inline u64 erlan_idx(u64 i, u64 len) {
    if (__builtin_expect(i >= len, 0)) erlan_panic("erlan: index out of bounds");
    return i;
}

/* Comprobación de rango para a[lo..hi]. */
static inline void erlan_range(u64 lo, u64 hi, u64 len) {
    if (__builtin_expect(lo > hi || hi > len, 0)) erlan_panic("erlan: slice range out of bounds");
}

/* Slices se generan por tipo de elemento: struct { T *ptr; u64 len; } */
#define ERLAN_SLICE(name, T) typedef struct name { T *ptr; u64 len; } name;

#endif
