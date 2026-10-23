/* Arranque de un servicio de usuario: _start en el primer byte del binario plano. */
#include "usys.h"

__asm__(
".section .text._start\n"
".global _start\n"
"_start:\n"
"  xorq %rbp, %rbp\n"
"  call main\n"
"  movq $0, %rax\n"
"  movq $0, %rdi\n"
"  int $0x80\n"
"1: jmp 1b\n"
);

void main(void);

__attribute__((noreturn)) void erlan_panic(const char *msg) {
    u64 n = 0; while (msg[n]) n++;
    usys(1, 0, (u64)(uintptr_t)msg, n);   /* log con el slot 0 */
    usys(0, 0xE, 0, 0);                    /* exit(14) */
    for (;;) {}
}

/* clang en freestanding puede emitir llamadas a estas rutinas. */
void *memset(void *s, int c, u64 n) { u8 *p = s; for (u64 i = 0; i < n; i++) p[i] = (u8)c; return s; }
void *memcpy(void *restrict d, const void *restrict s, u64 n) { u8 *dp = d; const u8 *sp = s; for (u64 i = 0; i < n; i++) dp[i] = sp[i]; return d; }
void *memmove(void *d, const void *s, u64 n) { u8 *dp = d; const u8 *sp = s; if (sp > dp) for (u64 i = 0; i < n; i++) dp[i] = sp[i]; else for (u64 i = n; i > 0; i--) dp[i-1] = sp[i-1]; return d; }
int memcmp(const void *a, const void *b, u64 n) { const u8 *p = a, *q = b; for (u64 i = 0; i < n; i++) if (p[i] != q[i]) return p[i] < q[i] ? -1 : 1; return 0; }
