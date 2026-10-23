// Cliente DNS (RFC 1035) en Erlan: consulta A por UDP y análisis con punteros de compresión.

extern c "netplat.h" { fn netplat_now_ms() -> u64; fn netplat_log(s: str); }
extern c "wire.h" {
    fn be16_get(b: []u8, off: u64) -> u16;
    fn be16_put(b: []u8, off: u64, v: u16);
    fn be32_get(b: []u8, off: u64) -> u32;
}
extern c "netio.h" {
    fn netio_payload() -> []u8;
    fn netio_my_ip() -> u32;
    fn netio_send_udp(src_ip: u32, dst_ip: u32, sport: u16, dport: u16, payload_len: u64) -> bool;
    fn netio_wait_udp(port: u16, timeout_ms: u64, out: []u8) -> u64;
}

const DNS_PORT: u16 = 53;
const TYPE_A: u16 = 1;
const TYPE_CNAME: u16 = 5;
const CLASS_IN: u16 = 1;

var next_port: u16 = 49152;
var reply: [1024]u8;
var last_ttl: u32 = 0;

fn dns_last_ttl() -> u32 { return last_ttl; }

// Escribe el QNAME de `name` en p a partir de off. Devuelve el nuevo offset (0 si inválido).
fn put_qname(p: []u8, off: u64, name: str) -> u64 {
    var o = off;
    var label_pos = o;
    var label_len: u64 = 0;
    o += 1;
    unsafe {
        var i: u64 = 0;
        while name[i] != 0 {
            let c = name[i];
            if c == 46 {
                if label_len == 0 || label_len > 63 { return 0; }
                p[label_pos] = label_len as u8;
                label_pos = o;
                label_len = 0;
                o += 1;
            } else {
                p[o] = c;
                o += 1;
                label_len += 1;
            }
            i += 1;
            if o + 2 >= p.len { return 0; }
        }
    }
    if label_len == 0 || label_len > 63 { return 0; }
    p[label_pos] = label_len as u8;
    p[o] = 0;
    return o + 1;
}

// Salta un nombre (etiquetas o puntero) y devuelve el offset siguiente.
fn skip_name(msg: []u8, len: u64, off: u64) -> u64 {
    var o = off;
    var hops: u64 = 0;
    while o < len {
        let c = msg[o] as u64;
        if c == 0 { return o + 1; }
        if (c & 0xC0) == 0xC0 { return o + 2; }
        o += 1 + c;
        hops += 1;
        if hops > 64 { return len; }
    }
    return len;
}

// Resuelve un registro A. Devuelve la IP (0 si falla).
fn dns_resolve(name: str, server: u32, timeout_ms: u64) -> u32 {
    var attempt: u64 = 0;
    while attempt < 3 {
        attempt += 1;
        let id = ((netplat_now_ms() * 40503 + attempt * 7919) & 0xFFFF) as u16;
        let port = next_port;
        next_port += 1;
        if next_port < 49152 { next_port = 49152; }

        let p = netio_payload();
        be16_put(p, 0, id);
        be16_put(p, 2, 0x0100);      // RD
        be16_put(p, 4, 1);           // QDCOUNT
        be16_put(p, 6, 0);
        be16_put(p, 8, 0);
        be16_put(p, 10, 0);
        var o = put_qname(p, 12, name);
        if o == 0 { netplat_log("dns: nombre invalido"); return 0; }
        be16_put(p, o, TYPE_A);
        be16_put(p, o + 2, CLASS_IN);
        o += 4;
        if !netio_send_udp(netio_my_ip(), server, port, DNS_PORT, o) { netplat_log("dns: no se pudo enviar"); return 0; }

        let got = netio_wait_udp(port, timeout_ms, reply[..]);
        if got < 12 { netplat_log("dns: sin respuesta, reintento"); continue; }
        if be16_get(reply[..], 0) != id { continue; }
        let flags = be16_get(reply[..], 2);
        if (flags & 0x8000) == 0 { continue; }
        if (flags & 0x000F) != 0 { netplat_log("dns: rcode de error"); return 0; }
        let qd = be16_get(reply[..], 4) as u64;
        let an = be16_get(reply[..], 6) as u64;
        var q: u64 = 12;
        for i in 0..qd { q = skip_name(reply[..], got, q) + 4; }
        var a: u64 = 0;
        while a < an && q + 10 <= got {
            q = skip_name(reply[..], got, q);
            if q + 10 > got { break; }
            let rtype = be16_get(reply[..], q);
            let rclass = be16_get(reply[..], q + 2);
            let ttl = be32_get(reply[..], q + 4);
            let rdlen = be16_get(reply[..], q + 8) as u64;
            q += 10;
            if q + rdlen > got { break; }
            if rtype == TYPE_A && rclass == CLASS_IN && rdlen == 4 {
                last_ttl = ttl;
                return be32_get(reply[..], q);
            }
            q += rdlen;   // CNAME u otros: seguir hasta el A
            a += 1;
        }
        netplat_log("dns: respuesta sin registro A");
        return 0;
    }
    return 0;
}
