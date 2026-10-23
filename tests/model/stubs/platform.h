#ifndef NUMERIC_TEST_PLATFORM_H
#define NUMERIC_TEST_PLATFORM_H
#include "erlan_rt.h"
#include <stdio.h>
#include <time.h>
extern u64 numeric_cancel_after, numeric_cancel_polls;
static inline void platform_log(const char *s) { fprintf(stderr, "[numeric] %s\n", s); }
static inline u64 platform_now_ms(void) { return (u64)clock() * 1000 / CLOCKS_PER_SEC; }
static inline void platform_yield(void) {}
static inline bool platform_cancelled(void) {
    ++numeric_cancel_polls;
    return numeric_cancel_after && numeric_cancel_polls > numeric_cancel_after;
}
#endif
