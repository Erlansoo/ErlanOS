// L1 — panic del kernel y punto de entrada de erlan_panic (el que llama el runtime de
// Erlan al fallar una comprobación de índices). Sustituye a panic.c desde el 26/10/2026.
import serial;

@noreturn fn kpanic(msg: str) {
    kprintf("\n*** KERNEL PANIC: %s\n", msg);
    loop { unsafe { asm("cli; hlt" : : : "memory"); } }
}

@noreturn fn erlan_panic(msg: str) {
    kpanic(msg);
}
