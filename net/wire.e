// Pila de red de ErlanOS — utilidades de bajo nivel, en Erlan, sin plataforma.
// Orden de bytes de red (big-endian), checksum de internet (RFC 1071) y formateo.
// Las IPs se manejan como u32 en orden de host (a.b.c.d = a<<24 | b<<16 | c<<8 | d)
// y se escriben en el cable con be32_put.

fn be16_get(b: []u8, off: u64) -> u16 {
    return ((b[off] as u16) << 8) | (b[off + 1] as u16);
}

fn be16_put(b: []u8, off: u64, v: u16) {
    b[off] = (v >> 8) as u8;
    b[off + 1] = (v & 0xFF) as u8;
}

fn be32_get(b: []u8, off: u64) -> u32 {
    return ((b[off] as u32) << 24) | ((b[off + 1] as u32) << 16) | ((b[off + 2] as u32) << 8) | (b[off + 3] as u32);
}

fn be32_put(b: []u8, off: u64, v: u32) {
    b[off] = (v >> 24) as u8;
    b[off + 1] = ((v >> 16) & 0xFF) as u8;
    b[off + 2] = ((v >> 8) & 0xFF) as u8;
    b[off + 3] = (v & 0xFF) as u8;
}

// Suma en complemento a uno de palabras de 16 bits big-endian, plegada a 16 bits.
fn inet_sum(b: []u8, start: u64, len: u64, seed: u32) -> u32 {
    var s: u32 = seed;
    var i = start;
    let end = start + len;
    while i + 1 < end {
        s += ((b[i] as u32) << 8) | (b[i + 1] as u32);
        i += 2;
    }
    if i < end { s += (b[i] as u32) << 8; }
    while (s >> 16) != 0 { s = (s & 0xFFFF) + (s >> 16); }
    return s;
}

fn inet_csum(b: []u8, start: u64, len: u64, seed: u32) -> u16 {
    let s = inet_sum(b, start, len, seed);
    return (~s & 0xFFFF) as u16;
}

fn ip_make(a: u64, b: u64, c: u64, d: u64) -> u32 {
    return ((a as u32) << 24) | ((b as u32) << 16) | ((c as u32) << 8) | (d as u32);
}

fn mac_eq(a: []u8, ao: u64, b: []u8, bo: u64) -> bool {
    for i in 0..6 { if a[ao + i] != b[bo + i] { return false; } }
    return true;
}

fn mac_copy(dst: []u8, doff: u64, src: []u8, soff: u64) {
    for i in 0..6 { dst[doff + i] = src[soff + i]; }
}

fn mac_is_broadcast(m: []u8, off: u64) -> bool {
    for i in 0..6 { if m[off + i] != 0xFF { return false; } }
    return true;
}

// ---- formateo para logs y consola ----

fn fmt_str(buf: []u8, pos: u64, s: str) -> u64 {
    var p = pos;
    unsafe {
        var i: u64 = 0;
        while s[i] != 0 && p < buf.len { buf[p] = s[i]; p += 1; i += 1; }
    }
    return p;
}

fn fmt_u64(buf: []u8, pos: u64, v: u64) -> u64 {
    var tmp: [24]u8;
    var n: u64 = 0;
    var x = v;
    if x == 0 { tmp[0] = 48; n = 1; }
    while x > 0 { tmp[n] = 48 + ((x % 10) as u8); x = x / 10; n += 1; }
    var p = pos;
    while n > 0 && p < buf.len { n -= 1; buf[p] = tmp[n]; p += 1; }
    return p;
}

fn hex_digit(v: u8) -> u8 {
    if v < 10 { return 48 + v; }
    return 87 + v;   // 'a' = 97
}

fn fmt_hex8(buf: []u8, pos: u64, v: u8) -> u64 {
    if pos + 2 > buf.len { return pos; }
    buf[pos] = hex_digit(v >> 4);
    buf[pos + 1] = hex_digit(v & 0xF);
    return pos + 2;
}

fn fmt_mac(buf: []u8, pos: u64, m: []u8, off: u64) -> u64 {
    var p = pos;
    for i in 0..6 {
        p = fmt_hex8(buf, p, m[off + i]);
        if i < 5 && p < buf.len { buf[p] = 58; p += 1; }   // ':'
    }
    return p;
}

fn fmt_ip(buf: []u8, pos: u64, ip: u32) -> u64 {
    var p = pos;
    p = fmt_u64(buf, p, ((ip >> 24) & 0xFF) as u64);
    if p < buf.len { buf[p] = 46; p += 1; }
    p = fmt_u64(buf, p, ((ip >> 16) & 0xFF) as u64);
    if p < buf.len { buf[p] = 46; p += 1; }
    p = fmt_u64(buf, p, ((ip >> 8) & 0xFF) as u64);
    if p < buf.len { buf[p] = 46; p += 1; }
    p = fmt_u64(buf, p, (ip & 0xFF) as u64);
    return p;
}
