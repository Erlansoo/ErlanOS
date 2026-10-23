// Verificación ECDSA sobre P-256 y P-384 (FIPS 186-5, SEC 1) en Erlan.
// Aritmética de Montgomery con miembros de 32 bits, coordenadas jacobianas (a = -3)
// y el truco de Shamir para u1·G + u2·Q. Sólo verifica: maneja datos públicos
// (certificados y CertificateVerify de TLS 1.3), por eso no es de tiempo constante.

struct Mod { m: [12]u32; minv: u64; r2: [12]u32; one: [12]u32; len: u64; }

var fp: Mod;
var fq: Mod;
var cur_bits: u64 = 0;
var cur_b: [12]u32;
var cur_gx: [12]u32;
var cur_gy: [12]u32;
var unit: [12]u32;
var ebytes: [48]u8;
var mt: [16]u64;
var ta: [16]u64;
var pool: [480]u32;     // 40 elementos de 12 miembros

fn ec_el(k: u64) -> []u32 { return pool[k * 12..k * 12 + 12]; }

fn ec_hexval(c: u8) -> u64 {
    let v = c as u64;
    if v >= 97 { return v - 87; }
    if v >= 65 { return v - 55; }
    return v - 48;
}

// Hex big-endian de 8n caracteres -> n miembros little-endian.
fn ec_load_hex(dst: []u32, s: str, n: u64) {
    for i in 0..12 { dst[i] = 0; }
    unsafe {
        for i in 0..n {
            var v: u64 = 0;
            let at = (n - 1 - i) * 8;
            for j in 0..8 { v = (v << 4) | ec_hexval(s[at + j]); }
            dst[i] = v as u32;
        }
    }
}

// Bytes big-endian (4n) -> n miembros little-endian.
fn ec_from_bytes(dst: []u32, b: []u8, n: u64) {
    for i in 0..12 { dst[i] = 0; }
    for i in 0..n {
        let at = (n - 1 - i) * 4;
        dst[i] = ((b[at] as u32) << 24) | ((b[at + 1] as u32) << 16) | ((b[at + 2] as u32) << 8) | (b[at + 3] as u32);
    }
}

fn ec_copy(dst: []u32, src: []u32) { for i in 0..12 { dst[i] = src[i]; } }

fn ec_is_zero(a: []u32, n: u64) -> bool {
    var acc: u32 = 0;
    for i in 0..n { acc = acc | a[i]; }
    return acc == 0;
}

fn ec_eq(a: []u32, b: []u32, n: u64) -> bool {
    for i in 0..n { if a[i] != b[i] { return false; } }
    return true;
}

fn ec_lt_mod(a: []u32, c: ptr<Mod>) -> bool {
    var k = c.len;
    while k > 0 {
        k -= 1;
        if a[k] < c.m[k] { return true; }
        if a[k] > c.m[k] { return false; }
    }
    return false;
}

// r = ta[0..n] (+ top·2^(32n)) reducido una vez módulo m.
fn ec_finish(c: ptr<Mod>, r: []u32, top: u64) {
    let n = c.len;
    var ge = top != 0;
    if !ge {
        ge = true;
        var k = n;
        while k > 0 {
            k -= 1;
            let mv = c.m[k] as u64;
            if ta[k] > mv { break; }
            if ta[k] < mv { ge = false; break; }
        }
    }
    if ge {
        var borrow: u64 = 0;
        for j in 0..n {
            let mv = (c.m[j] as u64) + borrow;
            if ta[j] >= mv { r[j] = (ta[j] - mv) as u32; borrow = 0; }
            else { r[j] = (ta[j] + 0x100000000 - mv) as u32; borrow = 1; }
        }
    } else {
        for j in 0..n { r[j] = ta[j] as u32; }
    }
}

// Montgomery CIOS: r = a·b·R^-1 mod m. r puede coincidir con a o b.
fn ec_mont_mul(c: ptr<Mod>, r: []u32, a: []u32, b: []u32) {
    let n = c.len;
    for i in 0..n + 2 { mt[i] = 0; }
    for i in 0..n {
        var carry: u64 = 0;
        let bi = b[i] as u64;
        for j in 0..n {
            let s = mt[j] + (a[j] as u64) * bi + carry;
            mt[j] = s & 0xFFFFFFFF;
            carry = s >> 32;
        }
        let s2 = mt[n] + carry;
        mt[n] = s2 & 0xFFFFFFFF;
        mt[n + 1] = s2 >> 32;
        let mf = (mt[0] * c.minv) & 0xFFFFFFFF;
        let s3 = mt[0] + mf * (c.m[0] as u64);
        carry = s3 >> 32;
        for j in 1..n {
            let s = mt[j] + mf * (c.m[j] as u64) + carry;
            mt[j - 1] = s & 0xFFFFFFFF;
            carry = s >> 32;
        }
        let s4 = mt[n] + carry;
        mt[n - 1] = s4 & 0xFFFFFFFF;
        mt[n] = mt[n + 1] + (s4 >> 32);
        mt[n + 1] = 0;
    }
    for j in 0..n { ta[j] = mt[j]; }
    ec_finish(c, r, mt[n]);
}

fn ec_mod_add(c: ptr<Mod>, r: []u32, a: []u32, b: []u32) {
    var carry: u64 = 0;
    for j in 0..c.len {
        let s = (a[j] as u64) + (b[j] as u64) + carry;
        ta[j] = s & 0xFFFFFFFF;
        carry = s >> 32;
    }
    ec_finish(c, r, carry);
}

fn ec_mod_sub(c: ptr<Mod>, r: []u32, a: []u32, b: []u32) {
    let n = c.len;
    var borrow: u64 = 0;
    for j in 0..n {
        let bv = (b[j] as u64) + borrow;
        let av = a[j] as u64;
        if av >= bv { ta[j] = av - bv; borrow = 0; } else { ta[j] = av + 0x100000000 - bv; borrow = 1; }
    }
    if borrow == 1 {
        var carry: u64 = 0;
        for j in 0..n {
            let s = ta[j] + (c.m[j] as u64) + carry;
            ta[j] = s & 0xFFFFFFFF;
            carry = s >> 32;
        }
    }
    for j in 0..n { r[j] = ta[j] as u32; }
}

fn ec_mod_setup(c: ptr<Mod>, hex: str, n: u64) {
    c.len = n;
    ec_load_hex(c.m[..], hex, n);
    let m0 = c.m[0] as u64;
    var inv: u64 = 1;
    for k in 0..5 {
        let x = (m0 * inv) & 0xFFFFFFFF;
        inv = (inv * ((2 + 0x100000000 - x) & 0xFFFFFFFF)) & 0xFFFFFFFF;
    }
    c.minv = (0x100000000 - inv) & 0xFFFFFFFF;
    var x = ec_el(39);
    for i in 0..12 { x[i] = 0; }
    x[0] = 1;
    for k in 0..32 * n { ec_mod_add(c, x, x, x); }
    for i in 0..12 { c.one[i] = x[i]; }
    for k in 0..32 * n { ec_mod_add(c, x, x, x); }
    for i in 0..12 { c.r2[i] = x[i]; }
}

fn ec_to_mont(c: ptr<Mod>, r: []u32, a: []u32) { ec_mont_mul(c, r, a, c.r2[..]); }
fn ec_from_mont(c: ptr<Mod>, r: []u32, a: []u32) { ec_mont_mul(c, r, a, unit[..]); }

// r = a^e (a en Montgomery, e entero normal).
fn ec_mod_pow(c: ptr<Mod>, r: []u32, a: []u32, e: []u32) {
    var acc = ec_el(38);
    var base = ec_el(37);
    ec_copy(acc, c.one[..]);
    ec_copy(base, a);
    var bit = c.len * 32;
    while bit > 0 {
        bit -= 1;
        ec_mont_mul(c, acc, acc, acc);
        if ((e[bit / 32] >> ((bit % 32) as u32)) & 1) != 0 { ec_mont_mul(c, acc, acc, base); }
    }
    ec_copy(r, acc);
}

// Inverso por Fermat (m primo): a^(m-2).
fn ec_mod_inv(c: ptr<Mod>, r: []u32, a: []u32) {
    var e = ec_el(36);
    ec_copy(e, c.m[..]);
    var borrow: u64 = 2;
    var j: u64 = 0;
    while borrow != 0 && j < c.len {
        let v = e[j] as u64;
        if v >= borrow { e[j] = (v - borrow) as u32; borrow = 0; }
        else { e[j] = (v + 0x100000000 - borrow) as u32; borrow = 1; }
        j += 1;
    }
    ec_mod_pow(c, r, a, e);
}

fn ec_curve_setup(bits: u64) -> bool {
    if cur_bits == bits { return true; }
    for i in 0..12 { unit[i] = 0; }
    unit[0] = 1;
    if bits == 256 {
        ec_mod_setup(&fp, "ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 8);
        ec_mod_setup(&fq, "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 8);
        ec_load_hex(cur_b[..], "5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 8);
        ec_load_hex(cur_gx[..], "6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296", 8);
        ec_load_hex(cur_gy[..], "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5", 8);
    } else if bits == 384 {
        ec_mod_setup(&fp, "fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe"
                          "ffffffff0000000000000000ffffffff", 12);
        ec_mod_setup(&fq, "ffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf"
                          "581a0db248b0a77aecec196accc52973", 12);
        ec_load_hex(cur_b[..], "b3312fa7e23ee7e4988e056be3f82d19181d9c6efe8141120314088f5013875a"
                               "c656398d8a2ed19d2a85c8edd3ec2aef", 12);
        ec_load_hex(cur_gx[..], "aa87ca22be8b05378eb1c71ef320ad746e1d3b628ba79b9859f741e082542a38"
                                "5502f25dbf55296c3a545e3872760ab7", 12);
        ec_load_hex(cur_gy[..], "3617de4a96262c6f5d9e98bf9292dc29f8f41dbd289a147ce9da3113b5f0b8c0"
                                "0a60b1ce1d7e819d7a431d7c90ea0e5f", 12);
    } else {
        return false;
    }
    ec_to_mont(&fp, cur_b[..], cur_b[..]);
    ec_to_mont(&fp, cur_gx[..], cur_gx[..]);
    ec_to_mont(&fp, cur_gy[..], cur_gy[..]);
    cur_bits = bits;
    return true;
}

fn ec_fm(r: []u32, a: []u32, b: []u32) { ec_mont_mul(&fp, r, a, b); }
fn ec_fa(r: []u32, a: []u32, b: []u32) { ec_mod_add(&fp, r, a, b); }
fn ec_fs(r: []u32, a: []u32, b: []u32) { ec_mod_sub(&fp, r, a, b); }

// Punto jacobiano en ranuras k, k+1, k+2 del pool. Z = 0 es el infinito.
fn ec_dbl(k: u64) {
    var x = ec_el(k);
    var y = ec_el(k + 1);
    var z = ec_el(k + 2);
    if ec_is_zero(z, fp.len) { return; }
    var delta = ec_el(12);
    var gamma = ec_el(13);
    var beta = ec_el(14);
    var alpha = ec_el(15);
    var t1 = ec_el(16);
    var t2 = ec_el(17);
    ec_fm(delta, z, z);
    ec_fm(gamma, y, y);
    ec_fm(beta, x, gamma);
    ec_fs(t1, x, delta);
    ec_fa(t2, x, delta);
    ec_fm(alpha, t1, t2);
    ec_fa(t1, alpha, alpha);
    ec_fa(alpha, t1, alpha);                 // alpha = 3(X-δ)(X+δ)
    ec_fa(t1, y, z);
    ec_fm(t1, t1, t1);
    ec_fs(t1, t1, gamma);
    ec_fs(z, t1, delta);                     // Z3 = (Y+Z)^2 - γ - δ
    ec_fa(t2, beta, beta);
    ec_fa(t2, t2, t2);                       // 4β
    ec_fm(t1, alpha, alpha);
    ec_fs(x, t1, t2);
    ec_fs(x, x, t2);                         // X3 = α^2 - 8β
    ec_fs(t2, t2, x);
    ec_fm(t2, alpha, t2);
    ec_fm(t1, gamma, gamma);
    ec_fa(t1, t1, t1);
    ec_fa(t1, t1, t1);
    ec_fa(t1, t1, t1);                       // 8γ^2
    ec_fs(y, t2, t1);                        // Y3 = α(4β - X3) - 8γ^2
}

// R(k) = R(k) + P(p)
fn ec_add(k: u64, p: u64) {
    var x1 = ec_el(k);
    var y1 = ec_el(k + 1);
    var z1 = ec_el(k + 2);
    var x2 = ec_el(p);
    var y2 = ec_el(p + 1);
    var z2 = ec_el(p + 2);
    if ec_is_zero(z2, fp.len) { return; }
    if ec_is_zero(z1, fp.len) { ec_copy(x1, x2); ec_copy(y1, y2); ec_copy(z1, z2); return; }
    var z1z1 = ec_el(18);
    var z2z2 = ec_el(19);
    var u1 = ec_el(20);
    var u2 = ec_el(21);
    var s1 = ec_el(22);
    var s2 = ec_el(23);
    var h = ec_el(24);
    var rr = ec_el(25);
    var ii = ec_el(26);
    var jj = ec_el(27);
    var v = ec_el(28);
    var t1 = ec_el(16);
    var t2 = ec_el(17);
    ec_fm(z1z1, z1, z1);
    ec_fm(z2z2, z2, z2);
    ec_fm(u1, x1, z2z2);
    ec_fm(u2, x2, z1z1);
    ec_fm(s1, y1, z2);
    ec_fm(s1, s1, z2z2);
    ec_fm(s2, y2, z1);
    ec_fm(s2, s2, z1z1);
    ec_fs(h, u2, u1);
    ec_fs(rr, s2, s1);
    if ec_is_zero(h, fp.len) {
        if ec_is_zero(rr, fp.len) { ec_dbl(k); return; }
        for i in 0..12 { z1[i] = 0; }
        return;
    }
    ec_fa(rr, rr, rr);                       // r = 2(S2 - S1)
    ec_fa(t1, h, h);
    ec_fm(ii, t1, t1);                       // I = (2H)^2
    ec_fm(jj, h, ii);                        // J = H·I
    ec_fm(v, u1, ii);                        // V = U1·I
    ec_fa(t1, z1, z2);
    ec_fm(t1, t1, t1);
    ec_fs(t1, t1, z1z1);
    ec_fs(t1, t1, z2z2);
    ec_fm(z1, t1, h);                        // Z3
    ec_fm(t1, rr, rr);
    ec_fs(t1, t1, jj);
    ec_fs(t1, t1, v);
    ec_fs(x1, t1, v);                        // X3 = r^2 - J - 2V
    ec_fs(t1, v, x1);
    ec_fm(t1, rr, t1);
    ec_fm(t2, s1, jj);
    ec_fa(t2, t2, t2);
    ec_fs(y1, t1, t2);                       // Y3 = r(V - X3) - 2·S1·J
}

fn ec_bit(e: []u32, i: u64) -> u32 { return (e[i / 32] >> ((i % 32) as u32)) & 1; }

// pubxy = X || Y (sin el prefijo 04), hash = resumen del mensaje, sig = r || s.
fn ecdsa_verify(bits: u64, pubxy: []u8, hash: []u8, sig: []u8) -> bool {
    if !ec_curve_setup(bits) { return false; }
    let n = fp.len;
    let nb = n * 4;
    if pubxy.len != 2 * nb || sig.len != 2 * nb || hash.len == 0 { return false; }
    var r = ec_el(29);
    var s = ec_el(30);
    var e = ec_el(31);
    var w = ec_el(32);
    var u1 = ec_el(33);
    var u2 = ec_el(34);
    var tmp = ec_el(35);
    ec_from_bytes(r, sig[0..nb], n);
    ec_from_bytes(s, sig[nb..2 * nb], n);
    if ec_is_zero(r, n) || ec_is_zero(s, n) || !ec_lt_mod(r, &fq) || !ec_lt_mod(s, &fq) { return false; }
    for i in 0..48 { ebytes[i] = 0; }
    if hash.len >= nb {
        for i in 0..nb { ebytes[i] = hash[i]; }
    } else {
        let pad = nb - hash.len;
        for i in 0..hash.len { ebytes[pad + i] = hash[i]; }
    }
    ec_from_bytes(e, ebytes[0..nb], n);
    for j in 0..n { ta[j] = e[j] as u64; }
    ec_finish(&fq, e, 0);
    ec_to_mont(&fq, tmp, s);
    ec_mod_inv(&fq, w, tmp);                 // w = s^-1 (Montgomery)
    ec_to_mont(&fq, tmp, e);
    ec_mont_mul(&fq, u1, tmp, w);
    ec_from_mont(&fq, u1, u1);               // u1 = e·w
    ec_to_mont(&fq, tmp, r);
    ec_mont_mul(&fq, u2, tmp, w);
    ec_from_mont(&fq, u2, u2);               // u2 = r·w

    var qx = ec_el(6);
    var qy = ec_el(7);
    var qz = ec_el(8);
    ec_from_bytes(qx, pubxy[0..nb], n);
    ec_from_bytes(qy, pubxy[nb..2 * nb], n);
    if !ec_lt_mod(qx, &fp) || !ec_lt_mod(qy, &fp) { return false; }
    ec_to_mont(&fp, qx, qx);
    ec_to_mont(&fp, qy, qy);
    ec_copy(qz, fp.one[..]);
    var lhs = ec_el(26);
    var rhs = ec_el(27);
    ec_fm(lhs, qy, qy);
    ec_fm(rhs, qx, qx);
    ec_fm(rhs, rhs, qx);
    ec_fs(rhs, rhs, qx);
    ec_fs(rhs, rhs, qx);
    ec_fs(rhs, rhs, qx);
    ec_fa(rhs, rhs, cur_b[..]);
    if !ec_eq(lhs, rhs, n) { return false; }  // Q no está en la curva

    ec_copy(ec_el(3), cur_gx[..]);
    ec_copy(ec_el(4), cur_gy[..]);
    ec_copy(ec_el(5), fp.one[..]);
    ec_copy(ec_el(9), ec_el(3));
    ec_copy(ec_el(10), ec_el(4));
    ec_copy(ec_el(11), ec_el(5));
    ec_add(9, 6);                            // G + Q
    ec_copy(ec_el(0), fp.one[..]);
    ec_copy(ec_el(1), fp.one[..]);
    for i in 0..12 { pool[24 + i] = 0; }     // R = infinito
    var i = n * 32;
    while i > 0 {
        i -= 1;
        ec_dbl(0);
        let b1 = ec_bit(u1, i);
        let b2 = ec_bit(u2, i);
        if b1 == 1 && b2 == 1 { ec_add(0, 9); }
        else if b1 == 1 { ec_add(0, 3); }
        else if b2 == 1 { ec_add(0, 6); }
    }
    if ec_is_zero(ec_el(2), n) { return false; }
    var zinv = ec_el(26);
    var xa = ec_el(27);
    ec_fm(zinv, ec_el(2), ec_el(2));
    ec_mod_inv(&fp, zinv, zinv);
    ec_fm(xa, ec_el(0), zinv);
    ec_from_mont(&fp, xa, xa);
    for j in 0..n { ta[j] = xa[j] as u64; }
    ec_finish(&fq, xa, 0);                   // v = x mod n
    return ec_eq(xa, r, n);
}
