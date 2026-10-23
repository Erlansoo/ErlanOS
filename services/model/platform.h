/* Plataforma para el model runtime dentro de ErlanOS: todo pasa por syscalls con capability.
 * (services/model/host/platform.h ofrece la misma interfaz sobre libc para pruebas.) */
#ifndef MODEL_PLATFORM_H
#define MODEL_PLATFORM_H
#include "erlan_rt.h"
#include "usys.h"

#define CAP_LOG_SLOT     0
#define CAP_TIME_SLOT    1
#define CAP_CONSOLE_SLOT 3
#define CAP_INPUT_SLOT   4
#define CAP_MODULE_SLOT  5
#define CAP_MEMORY_SLOT  6

#define HALO_IDLE          0
#define HALO_LISTENING     1
#define HALO_LOCAL         2
#define HALO_ACTING        3
#define HALO_AUTHORIZATION 4
#define HALO_CLOUD         5

static inline void platform_write(const u8 *p, u64 n) { usys(8, CAP_CONSOLE_SLOT, (u64)(uintptr_t)p, n); }
static inline void platform_log(const char *s) { u64 n = 0; while (s[n]) n++; usys(1, CAP_LOG_SLOT, (u64)(uintptr_t)s, n); }
static inline u64 platform_now_ms(void) { return usys(7, CAP_TIME_SLOT, 0, 0); }
static inline u8 platform_getkey(void) { return (u8)usys(9, CAP_INPUT_SLOT, 0, 0); }
static inline bool platform_cancelled(void) { return usys(17, CAP_INPUT_SLOT, 0, 0) == 1; }
static inline void platform_clear_cancel(void) { usys(17, CAP_INPUT_SLOT, 1, 0); }
static inline void platform_halo(u64 state) { usys(13, CAP_CONSOLE_SLOT, state, 0); }
static inline void platform_yield(void) { usys(2, 0, 0, 0); }
static inline void platform_sleep_ms(u64 ms) { usys(4, ms, 0, 0); }
static inline u64 platform_map_module(u64 idx) { return usys(10, CAP_MODULE_SLOT, idx, 0); }
static inline u64 platform_module_size(u64 idx) { return usys(11, CAP_MODULE_SLOT, idx, 0); }
static inline u64 platform_map_anon(u64 pages) { return usys(12, CAP_MEMORY_SLOT, pages, 0); }

#endif
