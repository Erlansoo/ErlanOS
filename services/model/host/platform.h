/* Plataforma de host (libc) para probar el model runtime fuera de ErlanOS. */
#ifndef MODEL_PLATFORM_H
#define MODEL_PLATFORM_H
#include "erlan_rt.h"
#include <stdio.h>
#include <time.h>

#ifdef ERLAN_HOST_CANCEL_TEST
void host_write(const u8 *p, u64 n);
void host_log(const char *s);
static inline void platform_write(const u8 *p, u64 n) { host_write(p, n); }
static inline void platform_log(const char *s) { host_log(s); }
#else
static inline void platform_write(const u8 *p, u64 n) { fwrite(p, 1, n, stdout); fflush(stdout); }
static inline void platform_log(const char *s) { fprintf(stderr, "[model] %s\n", s); }
#endif
static inline u64 platform_now_ms(void) { return (u64)clock() * 1000 / CLOCKS_PER_SEC; }
static inline u8 platform_getkey(void) { int c = getchar(); return c < 0 ? 0 : (u8)c; }
#ifdef ERLAN_HOST_CANCEL_TEST
bool host_cancelled(void);
static inline bool platform_cancelled(void) { return host_cancelled(); }
#else
static inline bool platform_cancelled(void) { return false; }
#endif
static inline void platform_halo(u64 state) { (void)state; }
static inline void platform_yield(void) {}
static inline void platform_sleep_ms(u64 ms) { (void)ms; }

#endif
