// Arranque de un servicio de usuario: _start en el primer byte del binario plano (user.ld
// lo coloca en .text._start), erlan_panic por syscall y las rutinas de memoria que clang
// puede emitir en freestanding. Sustituye a crt0.c desde el 26/10/2026.
import usys;

asm(".section .text._start\n"
    ".global _start\n"
    "_start:\n"
    "  xorq %rbp, %rbp\n"
    "  call main\n"
    "  movq $0, %rax\n"
    "  movq $0, %rdi\n"
    "  int $0x80\n"
    "1: jmp 1b\n");

@noreturn fn erlan_panic(msg: str) {
    var n: u64 = 0;
    unsafe {
        while msg[n] != 0 { n += 1; }
        usys(1, 0, msg as ptr<u8> as u64, n);   // log con el slot 0
    }
    usys(0, 0xE, 0, 0);                        // exit(14)
    loop { }
}

fn memset(s: ptr<void>, c: i32, n: u64) -> ptr<void> {
    unsafe { let p = s as ptr<u8>; let v = c as u8; for i in 0..n { p[i] = v; } }
    return s;
}
fn memcpy(d: ptr<void>, s: ptr<void>, n: u64) -> ptr<void> {
    unsafe { let dp = d as ptr<u8>; let sp = s as ptr<u8>; for i in 0..n { dp[i] = sp[i]; } }
    return d;
}
fn memmove(d: ptr<void>, s: ptr<void>, n: u64) -> ptr<void> {
    unsafe {
        let dp = d as ptr<u8>; let sp = s as ptr<u8>;
        if (s as u64) > (d as u64) { for i in 0..n { dp[i] = sp[i]; } }
        else if (s as u64) < (d as u64) { var i = n; while i > 0 { dp[i - 1] = sp[i - 1]; i -= 1; } }
    }
    return d;
}
fn memcmp(a: ptr<void>, b: ptr<void>, n: u64) -> i32 {
    unsafe {
        let p = a as ptr<u8>; let q = b as ptr<u8>;
        for i in 0..n { if p[i] != q[i] { if p[i] < q[i] { return -1; } return 1; } }
    }
    return 0;
}
