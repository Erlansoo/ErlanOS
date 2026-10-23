// AES-128/256 (FIPS 197) y AES-GCM (NIST SP 800-38D) en Erlan.
// Sólo se implementa el cifrado de bloque: GCM usa modo contador en ambos sentidos.
// La S-box se calcula al iniciar a partir del inverso en GF(2^8); no hay tablas copiadas.
// Límite declarado: GHASH bit a bit y sin tiempo constante (suficiente para v1).

struct Aes { rk: [240]u8; rounds: u64; st: [16]u8; tmp: [16]u8; hh: u64; hl: u64; }

var sbox: [256]u8;
var sbox_ready: bool = false;
var one: Aes;
var gx_h: u64 = 0;
var gx_l: u64 = 0;
var gm_h: u64 = 0;
var gm_l: u64 = 0;

fn aes_xt(b: u64) -> u64 { return ((b << 1) ^ (((b >> 7) & 1) * 0x1b)) & 0xff; }
fn aes_rotl8(x: u64, s: u64) -> u64 { return ((x << s) | (x >> (8 - s))) & 0xff; }

fn aes_init_sbox() {
    if sbox_ready { return; }
    var p: u64 = 1;
    var q: u64 = 1;
    loop {
        p = (p ^ aes_xt(p)) & 0xff;              // p * 3
        q = (q ^ (q << 1)) & 0xff;           // q / 3
        q = (q ^ (q << 2)) & 0xff;
        q = (q ^ (q << 4)) & 0xff;
        if (q & 0x80) != 0 { q = q ^ 0x09; }
        let x = q ^ aes_rotl8(q, 1) ^ aes_rotl8(q, 2) ^ aes_rotl8(q, 3) ^ aes_rotl8(q, 4);
        sbox[p] = ((x ^ 0x63) & 0xff) as u8;
        if p == 1 { break; }
    }
    sbox[0] = 0x63;
    sbox_ready = true;
}

fn aes_sb(x: u64) -> u64 { return sbox[x & 0xff] as u64; }

fn aes_be64(b: []u8, off: u64) -> u64 {
    var v: u64 = 0;
    for i in 0..8 { v = (v << 8) | (b[off + i] as u64); }
    return v;
}

fn aes_put_be64(b: []u8, off: u64, v: u64) {
    for i in 0..8 { b[off + i] = ((v >> (56 - 8 * i)) & 0xff) as u8; }
}

fn aes_encrypt_block(c: ptr<Aes>, inp: []u8, out: []u8) {
    for i in 0..16 { c.st[i] = inp[i] ^ c.rk[i]; }
    var round: u64 = 1;
    while round <= c.rounds {
        // SubBytes + ShiftRows (estado por columnas: fila r, columna k -> r + 4k)
        for k in 0..4 {
            for r in 0..4 {
                c.tmp[r + 4 * k] = sbox[c.st[r + 4 * ((k + r) % 4)] as u64];
            }
        }
        if round != c.rounds {
            for k in 0..4 {
                let a0 = c.tmp[4 * k] as u64;
                let a1 = c.tmp[4 * k + 1] as u64;
                let a2 = c.tmp[4 * k + 2] as u64;
                let a3 = c.tmp[4 * k + 3] as u64;
                c.st[4 * k] = (aes_xt(a0) ^ aes_xt(a1) ^ a1 ^ a2 ^ a3) as u8;
                c.st[4 * k + 1] = (a0 ^ aes_xt(a1) ^ aes_xt(a2) ^ a2 ^ a3) as u8;
                c.st[4 * k + 2] = (a0 ^ a1 ^ aes_xt(a2) ^ aes_xt(a3) ^ a3) as u8;
                c.st[4 * k + 3] = (aes_xt(a0) ^ a0 ^ a1 ^ a2 ^ aes_xt(a3)) as u8;
            }
        } else {
            for k in 0..16 { c.st[k] = c.tmp[k]; }
        }
        for k in 0..16 { c.st[k] = c.st[k] ^ c.rk[round * 16 + k]; }
        round += 1;
    }
    for i in 0..16 { out[i] = c.st[i]; }
}

// Expande la clave (16 o 32 bytes) y precalcula H = E_K(0^128) para GHASH.
fn aes_setup(c: ptr<Aes>, key: []u8) -> bool {
    aes_init_sbox();
    var nk: u64 = 0;
    if key.len == 16 { nk = 4; c.rounds = 10; }
    else if key.len == 32 { nk = 8; c.rounds = 14; }
    else { return false; }
    for i in 0..240 { c.rk[i] = 0; }
    for i in 0..key.len { c.rk[i] = key[i]; }
    let total = 4 * (c.rounds + 1);
    var rcon: u64 = 1;
    var w = nk;
    while w < total {
        var t0 = c.rk[(w - 1) * 4] as u64;
        var t1 = c.rk[(w - 1) * 4 + 1] as u64;
        var t2 = c.rk[(w - 1) * 4 + 2] as u64;
        var t3 = c.rk[(w - 1) * 4 + 3] as u64;
        if w % nk == 0 {
            let first = t0;
            t0 = aes_sb(t1) ^ rcon;
            t1 = aes_sb(t2);
            t2 = aes_sb(t3);
            t3 = aes_sb(first);
            rcon = aes_xt(rcon);
        } else if nk > 6 && w % nk == 4 {
            t0 = aes_sb(t0); t1 = aes_sb(t1); t2 = aes_sb(t2); t3 = aes_sb(t3);
        }
        c.rk[w * 4] = ((c.rk[(w - nk) * 4] as u64) ^ t0) as u8;
        c.rk[w * 4 + 1] = ((c.rk[(w - nk) * 4 + 1] as u64) ^ t1) as u8;
        c.rk[w * 4 + 2] = ((c.rk[(w - nk) * 4 + 2] as u64) ^ t2) as u8;
        c.rk[w * 4 + 3] = ((c.rk[(w - nk) * 4 + 3] as u64) ^ t3) as u8;
        w += 1;
    }
    var zero: [16]u8;
    var h: [16]u8;
    for i in 0..16 { zero[i] = 0; }
    aes_encrypt_block(c, zero[..], h[..]);
    c.hh = aes_be64(h[..], 0);
    c.hl = aes_be64(h[..], 8);
    return true;
}

// Producto en GF(2^128) con la reducción de GCM; resultado en gm_h:gm_l.
fn aes_gf_mul(xh: u64, xl: u64, yh: u64, yl: u64) {
    var zh: u64 = 0;
    var zl: u64 = 0;
    var vh = yh;
    var vl = yl;
    for i in 0..128 {
        var bit: u64 = 0;
        if i < 64 { bit = (xh >> (63 - i)) & 1; } else { bit = (xl >> (127 - i)) & 1; }
        if bit == 1 { zh = zh ^ vh; zl = zl ^ vl; }
        let lsb = vl & 1;
        vl = (vl >> 1) | (vh << 63);
        vh = vh >> 1;
        if lsb == 1 { vh = vh ^ 0xE100000000000000; }
    }
    gm_h = zh;
    gm_l = zl;
}

fn aes_ghash_bytes(c: ptr<Aes>, data: []u8) {
    var off: u64 = 0;
    var blk: [16]u8;
    while off < data.len {
        for k in 0..16 { blk[k] = 0; }
        var n = data.len - off;
        if n > 16 { n = 16; }
        for k in 0..n { blk[k] = data[off + k]; }
        aes_gf_mul(gx_h ^ aes_be64(blk[..], 0), gx_l ^ aes_be64(blk[..], 8), c.hh, c.hl);
        gx_h = gm_h;
        gx_l = gm_l;
        off += 16;
    }
}

fn gcm_j0(iv: []u8, j0: []u8) {
    for k in 0..12 { j0[k] = iv[k]; }
    j0[12] = 0; j0[13] = 0; j0[14] = 0; j0[15] = 1;
}

fn gcm_tag(c: ptr<Aes>, j0: []u8, aad: []u8, ct: []u8, tag: []u8) {
    gx_h = 0;
    gx_l = 0;
    aes_ghash_bytes(c, aad);
    aes_ghash_bytes(c, ct);
    aes_gf_mul(gx_h ^ (aad.len * 8), gx_l ^ (ct.len * 8), c.hh, c.hl);
    var s: [16]u8;
    var ek: [16]u8;
    aes_put_be64(s[..], 0, gm_h);
    aes_put_be64(s[..], 8, gm_l);
    aes_encrypt_block(c, j0, ek[..]);
    for k in 0..16 { tag[k] = s[k] ^ ek[k]; }
}

// Modo contador desde inc32(J0). inp y out pueden ser el mismo buffer.
fn gcm_ctr(c: ptr<Aes>, j0: []u8, inp: []u8, out: []u8) {
    var ctr: [16]u8;
    var ks: [16]u8;
    for k in 0..16 { ctr[k] = j0[k]; }
    var off: u64 = 0;
    while off < inp.len {
        var carry: u64 = 1;
        var k: u64 = 16;
        while k > 12 && carry == 1 {
            k -= 1;
            let v = (ctr[k] as u64) + 1;
            ctr[k] = (v & 0xff) as u8;
            if v < 256 { carry = 0; }
        }
        aes_encrypt_block(c, ctr[..], ks[..]);
        var n = inp.len - off;
        if n > 16 { n = 16; }
        for i in 0..n { out[off + i] = inp[off + i] ^ ks[i]; }
        off += 16;
    }
}

// out = cifrado || etiqueta de 16 bytes. IV de 12 bytes (el de TLS 1.3).
fn gcm_seal(c: ptr<Aes>, iv: []u8, aad: []u8, pt: []u8, out: []u8) -> bool {
    if iv.len != 12 || out.len < pt.len + 16 { return false; }
    var j0: [16]u8;
    gcm_j0(iv, j0[..]);
    gcm_ctr(c, j0[..], pt, out[0..pt.len]);
    gcm_tag(c, j0[..], aad, out[0..pt.len], out[pt.len..pt.len + 16]);
    return true;
}

// Verifica la etiqueta ANTES de descifrar. Devuelve false si no coincide.
fn gcm_open(c: ptr<Aes>, iv: []u8, aad: []u8, ct: []u8, out: []u8) -> bool {
    if iv.len != 12 || ct.len < 16 { return false; }
    let n = ct.len - 16;
    if out.len < n { return false; }
    var j0: [16]u8;
    var tag: [16]u8;
    gcm_j0(iv, j0[..]);
    gcm_tag(c, j0[..], aad, ct[0..n], tag[..]);
    var diff: u64 = 0;
    for k in 0..16 { diff = diff | ((tag[k] ^ ct[n + k]) as u64); }
    if diff != 0 { return false; }
    gcm_ctr(c, j0[..], ct[0..n], out[0..n]);
    return true;
}

fn aes_block_oneshot(key: []u8, inp: []u8, out: []u8) -> bool {
    if !aes_setup(&one, key) { return false; }
    aes_encrypt_block(&one, inp, out);
    return true;
}

fn gcm_seal_oneshot(key: []u8, iv: []u8, aad: []u8, pt: []u8, out: []u8) -> bool {
    if !aes_setup(&one, key) { return false; }
    return gcm_seal(&one, iv, aad, pt, out);
}

fn gcm_open_oneshot(key: []u8, iv: []u8, aad: []u8, ct: []u8, out: []u8) -> bool {
    if !aes_setup(&one, key) { return false; }
    return gcm_open(&one, iv, aad, ct, out);
}
