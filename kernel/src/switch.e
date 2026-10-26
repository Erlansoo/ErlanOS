// L1 — cambio de contexto entre pilas de kernel. Lo único de M2 que exige ensamblador.
// Sustituye a switch.c desde el 26/10/2026.
//
// switch_context(old_rsp, new_rsp): guarda los registros callee-saved en la pila actual,
// almacena rsp en *old_rsp, carga new_rsp, restaura registros y retorna en el nuevo contexto.
// thread_trampoline: un `iretq` que arranca un hilo nuevo con el frame preparado por sched.e.
asm(".text\n"
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
    "  iretq\n");

// Las etiquetas del ensamblador, vistas desde Erlan.
extern c {
    fn switch_context(old_rsp: ptr<u64>, new_rsp: u64);
    fn thread_trampoline();
}

fn thread_trampoline_addr() -> u64 { return thread_trampoline as u64; }
