// clang en freestanding puede emitir llamadas a estas cuatro funciones de la biblioteca de C
// aunque nadie las invoque explícitamente (copias de structs, inicializaciones). Deben
// existir con la ABI de C. Sustituye a string.c desde el 26/10/2026 (se llama kstring para no
// tapar el <string.h> de la biblioteca de C en las pruebas de host).

fn memcpy(dest: ptr<void>, src: ptr<void>, n: u64) -> ptr<void> {
    unsafe {
        let d = dest as ptr<u8>;
        let s = src as ptr<u8>;
        for i in 0..n { d[i] = s[i]; }
    }
    return dest;
}

fn memset(s: ptr<void>, c: i32, n: u64) -> ptr<void> {
    unsafe {
        let p = s as ptr<u8>;
        let v = c as u8;
        for i in 0..n { p[i] = v; }
    }
    return s;
}

fn memmove(dest: ptr<void>, src: ptr<void>, n: u64) -> ptr<void> {
    unsafe {
        let d = dest as ptr<u8>;
        let s = src as ptr<u8>;
        if (src as u64) > (dest as u64) { for i in 0..n { d[i] = s[i]; } }
        else if (src as u64) < (dest as u64) { var i = n; while i > 0 { d[i - 1] = s[i - 1]; i -= 1; } }
    }
    return dest;
}

fn memcmp(a: ptr<void>, b: ptr<void>, n: u64) -> i32 {
    unsafe {
        let p = a as ptr<u8>;
        let q = b as ptr<u8>;
        for i in 0..n {
            if p[i] != q[i] { if p[i] < q[i] { return -1; } return 1; }
        }
    }
    return 0;
}
