/* Implementación de erlan_panic para pruebas en host. */
#include <stdio.h>
#include <stdlib.h>
__attribute__((noreturn)) void erlan_panic(const char *msg) {
    printf("PANIC: %s\n", msg);
    fflush(stdout);
    exit(42);
}
