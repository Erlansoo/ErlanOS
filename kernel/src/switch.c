/* Cambio de contexto entre pilas de kernel. Lo único de M2 que exige ensamblador. */
#include "switch.h"

__asm__(
".text\n"
".global switch_context\n"
"switch_context:\n"
"  pushq %rbp\n"
"  pushq %rbx\n"
"  pushq %r12\n"
"  pushq %r13\n"
"  pushq %r14\n"
"  pushq %r15\n"
"  movq %rsp, (%rdi)\n"
"  movq %rsi, %rsp\n"
"  popq %r15\n"
"  popq %r14\n"
"  popq %r13\n"
"  popq %r12\n"
"  popq %rbx\n"
"  popq %rbp\n"
"  ret\n"
".global thread_trampoline\n"
"thread_trampoline:\n"
"  iretq\n"
);

extern void thread_trampoline(void);
u64 thread_trampoline_addr(void) { return (u64)(uintptr_t)thread_trampoline; }
