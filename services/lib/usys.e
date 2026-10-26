// Syscalls de ErlanOS para los servicios de usuario (L2): la instrucción int 0x80.
// Convención: rax = nr, rdi/rsi/rdx = argumentos, rax = retorno.
// Sustituye a usys.h (C) desde el 26/10/2026.

fn usys(nr: u64, a: u64, b: u64, c: u64) -> u64 {
    var r: u64 = 0;
    unsafe { asm("int $0x80" : "=a"(r) : "a"(nr), "D"(a), "S"(b), "d"(c) : "rcx", "r11", "memory"); }
    return r;
}
