// X25519 (RFC 7748) en Erlan. Diseño de TweetNaCl: elementos de campo de 16 miembros
// de 16 bits en i64, escalera de Montgomery con intercambio condicional sin ramas.
// Uso en TLS 1.3: intercambio de claves (grupo x25519, 0x001d).

var fe: [224]i64;      // 14 elementos de campo de 16 miembros
var tt: [31]i64;
var zz: [32]u8;
var base_u: [32]u8;

fn x25_el(k: u64) -> []i64 { return fe[k * 16..(k + 1) * 16]; }

fn x25_car(o: []i64) {
    for i in 0..16 {
        o[i] += 65536;
        let c = o[i] >> 16;
        if i < 15 { o[i + 1] += c - 1; } else { o[0] += 38 * (c - 1); }
        o[i] -= c * 65536;
    }
}

fn x25_sel(p: []i64, q: []i64, b: i64) {
    let mask: i64 = 0 - b;
    for i in 0..16 {
        let t = mask & (p[i] ^ q[i]);
        p[i] = p[i] ^ t;
        q[i] = q[i] ^ t;
    }
}

fn x25_fadd(o: []i64, a: []i64, b: []i64) { for i in 0..16 { o[i] = a[i] + b[i]; } }
fn x25_fsub(o: []i64, a: []i64, b: []i64) { for i in 0..16 { o[i] = a[i] - b[i]; } }

fn x25_fmul(o: []i64, a: []i64, b: []i64) {
    for i in 0..31 { tt[i] = 0; }
    for i in 0..16 {
        for j in 0..16 { tt[i + j] += a[i] * b[j]; }
    }
    for i in 0..15 { tt[i] += 38 * tt[i + 16]; }
    for i in 0..16 { o[i] = tt[i]; }
    x25_car(o);
    x25_car(o);
}

fn x25_finv(o: []i64, a: []i64) {
    var c = x25_el(10);
    for i in 0..16 { c[i] = a[i]; }
    var k: u64 = 254;                            // c = a ya cubre el bit 254 de p-2
    while k > 0 {
        k -= 1;                                  // 253 .. 0
        x25_fmul(c, c, c);
        if k != 2 && k != 4 { x25_fmul(c, c, a); }
    }
    for i in 0..16 { o[i] = c[i]; }
}

fn x25_unpack(o: []i64, n: []u8) {
    for i in 0..16 { o[i] = (n[2 * i] as i64) + (n[2 * i + 1] as i64) * 256; }
    o[15] = o[15] & 0x7fff;
}

fn x25_pack(out: []u8, n: []i64) {
    var t = x25_el(8);
    var m = x25_el(9);
    for i in 0..16 { t[i] = n[i]; }
    x25_car(t);
    x25_car(t);
    x25_car(t);
    for pass in 0..2 {
        m[0] = t[0] - 0xffed;
        for i in 1..15 {
            m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
            m[i - 1] = m[i - 1] & 0xffff;
        }
        m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
        let b = (m[15] >> 16) & 1;
        m[14] = m[14] & 0xffff;
        x25_sel(t, m, 1 - b);
    }
    for i in 0..16 {
        out[2 * i] = (t[i] & 0xff) as u8;
        out[2 * i + 1] = ((t[i] >> 8) & 0xff) as u8;
    }
}

// out = scalar · point. Devuelve false si el resultado es todo ceros (RFC 8446 §7.4.2).
fn x25519_scalarmult(out: []u8, scalar: []u8, point: []u8) -> bool {
    if out.len < 32 || scalar.len != 32 || point.len != 32 { return false; }
    for i in 0..32 { zz[i] = scalar[i]; }
    zz[31] = (zz[31] & 127) | 64;
    zz[0] = zz[0] & 248;
    var a = x25_el(0);
    var b = x25_el(1);
    var c = x25_el(2);
    var d = x25_el(3);
    var e = x25_el(4);
    var f = x25_el(5);
    var x = x25_el(6);
    var k121665 = x25_el(7);
    x25_unpack(x, point);
    for i in 0..16 { b[i] = x[i]; a[i] = 0; c[i] = 0; d[i] = 0; k121665[i] = 0; }
    a[0] = 1;
    d[0] = 1;
    k121665[0] = 0xDB41;                         // 121665 = 0x1DB41
    k121665[1] = 1;
    var bit: u64 = 255;
    while bit > 0 {
        bit -= 1;                                // 254 .. 0
        let r = (((zz[bit >> 3] as u64) >> (bit & 7)) & 1) as i64;
        x25_sel(a, b, r);
        x25_sel(c, d, r);
        x25_fadd(e, a, c);
        x25_fsub(a, a, c);
        x25_fadd(c, b, d);
        x25_fsub(b, b, d);
        x25_fmul(d, e, e);
        x25_fmul(f, a, a);
        x25_fmul(a, c, a);
        x25_fmul(c, b, e);
        x25_fadd(e, a, c);
        x25_fsub(a, a, c);
        x25_fmul(b, a, a);
        x25_fsub(c, d, f);
        x25_fmul(a, c, k121665);
        x25_fadd(a, a, d);
        x25_fmul(c, c, a);                       // z2 = E·(AA + a24·E)
        x25_fmul(a, d, f);                       // x2 = AA·BB
        x25_fmul(d, b, x);                       // z3 = x1·(DA - CB)^2
        x25_fmul(b, e, e);                       // x3 = (DA + CB)^2
        x25_sel(a, b, r);
        x25_sel(c, d, r);
    }
    x25_finv(c, c);
    x25_fmul(a, a, c);
    x25_pack(out[0..32], a);
    var acc: u64 = 0;
    for i in 0..32 { acc = acc | (out[i] as u64); }
    return acc != 0;
}

// Clave pública: scalar · 9.
fn x25519_public(out: []u8, scalar: []u8) -> bool {
    for i in 0..32 { base_u[i] = 0; }
    base_u[0] = 9;
    return x25519_scalarmult(out, scalar, base_u[..]);
}
