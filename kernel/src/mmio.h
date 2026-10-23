/* Accesos MMIO volátiles y barreras para los controladores de dispositivo en Erlan.
 * Un puntero normal en C generado puede ser reordenado o elevado por clang -O2;
 * los registros y los descriptores DMA escritos por hardware exigen volatile. */
#ifndef ERLANOS_MMIO_H
#define ERLANOS_MMIO_H
#include "erlan_rt.h"

static inline u8  mmio_read8(u64 a)  { return *(volatile u8  *)(uintptr_t)a; }
static inline u16 mmio_read16(u64 a) { return *(volatile u16 *)(uintptr_t)a; }
static inline u32 mmio_read32(u64 a) { return *(volatile u32 *)(uintptr_t)a; }
static inline u64 mmio_read64(u64 a) { return *(volatile u64 *)(uintptr_t)a; }
static inline void mmio_write8(u64 a, u8 v)   { *(volatile u8  *)(uintptr_t)a = v; }
static inline void mmio_write16(u64 a, u16 v) { *(volatile u16 *)(uintptr_t)a = v; }
static inline void mmio_write32(u64 a, u32 v) { *(volatile u32 *)(uintptr_t)a = v; }
static inline void mmio_write64(u64 a, u64 v) { *(volatile u64 *)(uintptr_t)a = v; }

/* mfence es base de x86-64 (SSE2 garantizado); ordena cargas y almacenamientos
 * antes de tocar un registro doorbell (TDT/RDT). */
static inline void cpu_mfence(void) { __asm__ volatile ("mfence" ::: "memory"); }
static inline void cpu_pause(void) { __asm__ volatile ("pause" ::: "memory"); }

#endif
