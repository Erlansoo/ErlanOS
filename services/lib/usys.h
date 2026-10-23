/* Syscalls de ErlanOS para servicios de usuario (L2). Única pieza en C del lado usuario:
 * la instrucción int 0x80. Convención: rax = nr, rdi/rsi/rdx = args, rax = retorno. */
#ifndef ERLANOS_USYS_H
#define ERLANOS_USYS_H
#include "erlan_rt.h"

static inline u64 usys(u64 nr, u64 a, u64 b, u64 c) {
    u64 r;
    __asm__ volatile ("int $0x80" : "=a"(r) : "a"(nr), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory");
    return r;
}

#endif
