// L1 — copias de memoria por dirección (u64) para el código del kernel.
// Sustituye a kmem.h (C) desde el 26/10/2026.

fn kmemcpy(dst: u64, src: u64, n: u64) {
    unsafe {
        let d = dst as ptr<u8>;
        let s = src as ptr<u8>;
        for i in 0..n { d[i] = s[i]; }
    }
}

fn kmemset(dst: u64, v: u8, n: u64) {
    unsafe {
        let d = dst as ptr<u8>;
        for i in 0..n { d[i] = v; }
    }
}

fn kstrlen(s: str) -> u64 {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } }
    return n;
}
