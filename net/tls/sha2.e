// SHA-256, SHA-384 y SHA-512 (FIPS 180-4), HMAC (RFC 2104), HKDF (RFC 5869) y
// HKDF-Expand-Label de TLS 1.3 (RFC 8446 §7.1) en Erlan.
// Constantes derivadas con aritmética entera exacta (raíces de los primeros primos).
// kind: 0 = SHA-256, 1 = SHA-384, 2 = SHA-512.

struct Hash { kind: u64; h32: [8]u32; w32: [64]u32; h64: [8]u64; w64: [80]u64; buf: [128]u8; n: u64; total: u64; }

var k256: [64]u32;
var k512: [80]u64;
var iv256: [8]u32;
var iv384: [8]u64;
var iv512: [8]u64;
var consts_ready: bool = false;

var one_hash: Hash;
var hm_inner: Hash;
var hm_outer: Hash;
var hm_key: [128]u8;
var hm_pad: [128]u8;
var hm_tmp: [64]u8;
var hk_zero: [64]u8;
var hk_t: [64]u8;
var hk_msg: [600]u8;
var hk_info: [520]u8;

fn sha2_hexval(c: u8) -> u64 {
    let v = c as u64;
    if v >= 97 { return v - 87; }
    if v >= 65 { return v - 55; }
    return v - 48;
}

fn sha2_load32(dst: []u32, s: str) {
    unsafe {
        for i in 0..dst.len {
            var v: u64 = 0;
            for j in 0..8 { v = (v << 4) | sha2_hexval(s[i * 8 + j]); }
            dst[i] = v as u32;
        }
    }
}

fn sha2_load64(dst: []u64, s: str) {
    unsafe {
        for i in 0..dst.len {
            var v: u64 = 0;
            for j in 0..16 { v = (v << 4) | sha2_hexval(s[i * 16 + j]); }
            dst[i] = v;
        }
    }
}

fn sha2_consts() {
    if consts_ready { return; }
    sha2_load32(k256[..],
        "428a2f9871374491b5c0fbcfe9b5dba53956c25b59f111f1923f82a4ab1c5ed5"
        "d807aa9812835b01243185be550c7dc372be5d7480deb1fe9bdc06a7c19bf174"
        "e49b69c1efbe47860fc19dc6240ca1cc2de92c6f4a7484aa5cb0a9dc76f988da"
        "983e5152a831c66db00327c8bf597fc7c6e00bf3d5a7914706ca635114292967"
        "27b70a852e1b21384d2c6dfc53380d13650a7354766a0abb81c2c92e92722c85"
        "a2bfe8a1a81a664bc24b8b70c76c51a3d192e819d6990624f40e3585106aa070"
        "19a4c1161e376c082748774c34b0bcb5391c0cb34ed8aa4a5b9cca4f682e6ff3"
        "748f82ee78a5636f84c878148cc7020890befffaa4506cebbef9a3f7c67178f2");
    sha2_load32(iv256[..], "6a09e667bb67ae853c6ef372a54ff53a510e527f9b05688c1f83d9ab5be0cd19");
    sha2_load64(k512[..],
        "428a2f98d728ae227137449123ef65cdb5c0fbcfec4d3b2fe9b5dba58189dbbc"
        "3956c25bf348b53859f111f1b605d019923f82a4af194f9bab1c5ed5da6d8118"
        "d807aa98a303024212835b0145706fbe243185be4ee4b28c550c7dc3d5ffb4e2"
        "72be5d74f27b896f80deb1fe3b1696b19bdc06a725c71235c19bf174cf692694"
        "e49b69c19ef14ad2efbe4786384f25e30fc19dc68b8cd5b5240ca1cc77ac9c65"
        "2de92c6f592b02754a7484aa6ea6e4835cb0a9dcbd41fbd476f988da831153b5"
        "983e5152ee66dfaba831c66d2db43210b00327c898fb213fbf597fc7beef0ee4"
        "c6e00bf33da88fc2d5a79147930aa72506ca6351e003826f142929670a0e6e70"
        "27b70a8546d22ffc2e1b21385c26c9264d2c6dfc5ac42aed53380d139d95b3df"
        "650a73548baf63de766a0abb3c77b2a881c2c92e47edaee692722c851482353b"
        "a2bfe8a14cf10364a81a664bbc423001c24b8b70d0f89791c76c51a30654be30"
        "d192e819d6ef5218d69906245565a910f40e35855771202a106aa07032bbd1b8"
        "19a4c116b8d2d0c81e376c085141ab532748774cdf8eeb9934b0bcb5e19b48a8"
        "391c0cb3c5c95a634ed8aa4ae3418acb5b9cca4f7763e373682e6ff3d6b2b8a3"
        "748f82ee5defb2fc78a5636f43172f6084c87814a1f0ab728cc702081a6439ec"
        "90befffa23631e28a4506cebde82bde9bef9a3f7b2c67915c67178f2e372532b"
        "ca273eceea26619cd186b8c721c0c207eada7dd6cde0eb1ef57d4f7fee6ed178"
        "06f067aa72176fba0a637dc5a2c898a6113f9804bef90dae1b710b35131c471b"
        "28db77f523047d8432caab7b40c724933c9ebe0a15c9bebc431d67c49c100d4c"
        "4cc5d4becb3e42b6597f299cfc657e2a5fcb6fab3ad6faec6c44198c4a475817");
    sha2_load64(iv512[..],
        "6a09e667f3bcc908bb67ae8584caa73b3c6ef372fe94f82ba54ff53a5f1d36f1"
        "510e527fade682d19b05688c2b3e6c1f1f83d9abfb41bd6b5be0cd19137e2179");
    sha2_load64(iv384[..],
        "cbbb9d5dc1059ed8629a292a367cd5079159015a3070dd17152fecd8f70e5939"
        "67332667ffc00b318eb44a8768581511db0c2e0d64f98fa747b5481dbefa4fa4");
    consts_ready = true;
}

fn hash_len(kind: u64) -> u64 {
    if kind == 0 { return 32; }
    if kind == 1 { return 48; }
    return 64;
}

fn hash_block_len(kind: u64) -> u64 {
    if kind == 0 { return 64; }
    return 128;
}

fn sha2_rotr32(x: u32, n: u32) -> u32 { return (x >> n) | (x << (32 - n)); }
fn sha2_rotr64(x: u64, n: u64) -> u64 { return (x >> n) | (x << (64 - n)); }

fn sha256_block(c: ptr<Hash>) {
    for i in 0..16 {
        c.w32[i] = ((c.buf[4 * i] as u32) << 24) | ((c.buf[4 * i + 1] as u32) << 16)
                 | ((c.buf[4 * i + 2] as u32) << 8) | (c.buf[4 * i + 3] as u32);
    }
    for i in 16..64 {
        let x = c.w32[i - 15];
        let y = c.w32[i - 2];
        let s0 = sha2_rotr32(x, 7) ^ sha2_rotr32(x, 18) ^ (x >> 3);
        let s1 = sha2_rotr32(y, 17) ^ sha2_rotr32(y, 19) ^ (y >> 10);
        c.w32[i] = c.w32[i - 16] + s0 + c.w32[i - 7] + s1;
    }
    var a = c.h32[0];
    var b = c.h32[1];
    var cc = c.h32[2];
    var d = c.h32[3];
    var e = c.h32[4];
    var f = c.h32[5];
    var g = c.h32[6];
    var hh = c.h32[7];
    for i in 0..64 {
        let t1 = hh + (sha2_rotr32(e, 6) ^ sha2_rotr32(e, 11) ^ sha2_rotr32(e, 25))
               + ((e & f) ^ ((e ^ 0xFFFFFFFF) & g)) + k256[i] + c.w32[i];
        let t2 = (sha2_rotr32(a, 2) ^ sha2_rotr32(a, 13) ^ sha2_rotr32(a, 22))
               + ((a & b) ^ (a & cc) ^ (b & cc));
        hh = g; g = f; f = e; e = d + t1;
        d = cc; cc = b; b = a; a = t1 + t2;
    }
    c.h32[0] += a; c.h32[1] += b; c.h32[2] += cc; c.h32[3] += d;
    c.h32[4] += e; c.h32[5] += f; c.h32[6] += g; c.h32[7] += hh;
}

fn sha512_block(c: ptr<Hash>) {
    for i in 0..16 {
        var v: u64 = 0;
        for j in 0..8 { v = (v << 8) | (c.buf[8 * i + j] as u64); }
        c.w64[i] = v;
    }
    for i in 16..80 {
        let x = c.w64[i - 15];
        let y = c.w64[i - 2];
        let s0 = sha2_rotr64(x, 1) ^ sha2_rotr64(x, 8) ^ (x >> 7);
        let s1 = sha2_rotr64(y, 19) ^ sha2_rotr64(y, 61) ^ (y >> 6);
        c.w64[i] = c.w64[i - 16] + s0 + c.w64[i - 7] + s1;
    }
    var a = c.h64[0];
    var b = c.h64[1];
    var cc = c.h64[2];
    var d = c.h64[3];
    var e = c.h64[4];
    var f = c.h64[5];
    var g = c.h64[6];
    var hh = c.h64[7];
    for i in 0..80 {
        let t1 = hh + (sha2_rotr64(e, 14) ^ sha2_rotr64(e, 18) ^ sha2_rotr64(e, 41))
               + ((e & f) ^ ((e ^ 0xFFFFFFFFFFFFFFFF) & g)) + k512[i] + c.w64[i];
        let t2 = (sha2_rotr64(a, 28) ^ sha2_rotr64(a, 34) ^ sha2_rotr64(a, 39))
               + ((a & b) ^ (a & cc) ^ (b & cc));
        hh = g; g = f; f = e; e = d + t1;
        d = cc; cc = b; b = a; a = t1 + t2;
    }
    c.h64[0] += a; c.h64[1] += b; c.h64[2] += cc; c.h64[3] += d;
    c.h64[4] += e; c.h64[5] += f; c.h64[6] += g; c.h64[7] += hh;
}

fn hash_block(c: ptr<Hash>) {
    if c.kind == 0 { sha256_block(c); } else { sha512_block(c); }
}

fn hash_init(c: ptr<Hash>, kind: u64) {
    sha2_consts();
    c.kind = kind;
    c.n = 0;
    c.total = 0;
    for i in 0..8 {
        c.h32[i] = iv256[i];
        if kind == 1 { c.h64[i] = iv384[i]; } else { c.h64[i] = iv512[i]; }
    }
}

fn hash_update(c: ptr<Hash>, data: []u8) {
    let bl = hash_block_len(c.kind);
    for i in 0..data.len {
        c.buf[c.n] = data[i];
        c.n += 1;
        if c.n == bl { hash_block(c); c.n = 0; }
    }
    c.total += data.len;
}

// Escribe hash_len(kind) bytes en out. El contexto queda consumido.
fn hash_final(c: ptr<Hash>, out: []u8) {
    let bl = hash_block_len(c.kind);
    let lenbytes = bl / 8;
    let bits = c.total * 8;
    c.buf[c.n] = 0x80;
    c.n += 1;
    if c.n > bl - lenbytes {
        while c.n < bl { c.buf[c.n] = 0; c.n += 1; }
        hash_block(c);
        c.n = 0;
    }
    while c.n < bl - 8 { c.buf[c.n] = 0; c.n += 1; }
    for i in 0..8 { c.buf[bl - 8 + i] = ((bits >> (56 - 8 * i)) & 0xff) as u8; }
    hash_block(c);
    let hl = hash_len(c.kind);
    for i in 0..hl {
        if c.kind == 0 {
            out[i] = ((c.h32[i / 4] >> (24 - 8 * ((i % 4) as u32))) & 0xff) as u8;
        } else {
            out[i] = ((c.h64[i / 8] >> (56 - 8 * (i % 8))) & 0xff) as u8;
        }
    }
}

// Copia el estado (para calcular un hash intermedio del transcript sin consumirlo).
fn hash_copy(dst: ptr<Hash>, src: ptr<Hash>) {
    dst.kind = src.kind;
    dst.n = src.n;
    dst.total = src.total;
    for i in 0..8 { dst.h32[i] = src.h32[i]; dst.h64[i] = src.h64[i]; }
    for i in 0..128 { dst.buf[i] = src.buf[i]; }
}

fn sha256_oneshot(data: []u8, out: []u8) { hash_init(&one_hash, 0); hash_update(&one_hash, data); hash_final(&one_hash, out); }
fn sha384_oneshot(data: []u8, out: []u8) { hash_init(&one_hash, 1); hash_update(&one_hash, data); hash_final(&one_hash, out); }
fn sha512_oneshot(data: []u8, out: []u8) { hash_init(&one_hash, 2); hash_update(&one_hash, data); hash_final(&one_hash, out); }

// HMAC de una pasada. out puede coincidir con key o msg.
fn hmac(kind: u64, key: []u8, msg: []u8, out: []u8) {
    let bl = hash_block_len(kind);
    let hl = hash_len(kind);
    for i in 0..128 { hm_key[i] = 0; }
    if key.len > bl {
        hash_init(&hm_inner, kind);
        hash_update(&hm_inner, key);
        hash_final(&hm_inner, hm_key[0..hl]);
    } else {
        for i in 0..key.len { hm_key[i] = key[i]; }
    }
    for i in 0..bl { hm_pad[i] = hm_key[i] ^ 0x36; }
    hash_init(&hm_inner, kind);
    hash_update(&hm_inner, hm_pad[0..bl]);
    hash_update(&hm_inner, msg);
    hash_final(&hm_inner, hm_tmp[0..hl]);
    for i in 0..bl { hm_pad[i] = hm_key[i] ^ 0x5c; }
    hash_init(&hm_outer, kind);
    hash_update(&hm_outer, hm_pad[0..bl]);
    hash_update(&hm_outer, hm_tmp[0..hl]);
    hash_final(&hm_outer, out[0..hl]);
}

fn hkdf_extract(kind: u64, salt: []u8, ikm: []u8, out: []u8) {
    let hl = hash_len(kind);
    if salt.len == 0 {
        for i in 0..64 { hk_zero[i] = 0; }
        hmac(kind, hk_zero[0..hl], ikm, out);
    } else {
        hmac(kind, salt, ikm, out);
    }
}

// HKDF-Expand. Si out coincide con prk, out.len no debe superar hash_len.
fn hkdf_expand(kind: u64, prk: []u8, info: []u8, out: []u8) -> bool {
    let hl = hash_len(kind);
    if out.len > 255 * hl || info.len > 520 { return false; }
    var done: u64 = 0;
    var counter: u64 = 1;
    var tlen: u64 = 0;
    while done < out.len {
        var m: u64 = 0;
        for i in 0..tlen { hk_msg[m] = hk_t[i]; m += 1; }
        for i in 0..info.len { hk_msg[m] = info[i]; m += 1; }
        hk_msg[m] = counter as u8;
        m += 1;
        hmac(kind, prk, hk_msg[0..m], hk_t[0..hl]);
        tlen = hl;
        var n = out.len - done;
        if n > hl { n = hl; }
        for i in 0..n { out[done + i] = hk_t[i]; }
        done += n;
        counter += 1;
    }
    return true;
}

// HKDF-Expand-Label(secret, label, context, length) con el prefijo "tls13 ".
fn hkdf_expand_label(kind: u64, secret: []u8, label: str, context: []u8, out: []u8) -> bool {
    var llen: u64 = 0;
    unsafe { while label[llen] != 0 { llen += 1; } }
    if llen > 249 || context.len > 255 || out.len > 65535 { return false; }
    hk_info[0] = ((out.len >> 8) & 0xff) as u8;
    hk_info[1] = (out.len & 0xff) as u8;
    hk_info[2] = (6 + llen) as u8;
    var p: u64 = 3;
    let prefix = "tls13 ";
    unsafe {
        for i in 0..6 { hk_info[p] = prefix[i]; p += 1; }
        for i in 0..llen { hk_info[p] = label[i]; p += 1; }
    }
    hk_info[p] = context.len as u8;
    p += 1;
    for i in 0..context.len { hk_info[p] = context[i]; p += 1; }
    return hkdf_expand(kind, secret, hk_info[0..p], out);
}
