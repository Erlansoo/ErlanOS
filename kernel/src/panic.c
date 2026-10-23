/* Panic del kernel y punto de entrada de erlan_panic para el código Erlan de L1. */
#include "serial.h"

__attribute__((noreturn)) void kpanic(const char *msg) {
    kprintf("\n*** KERNEL PANIC: %s\n", msg);
    for (;;) __asm__ volatile ("cli; hlt");
}

__attribute__((noreturn)) void erlan_panic(const char *msg) {
    kpanic(msg);
}
