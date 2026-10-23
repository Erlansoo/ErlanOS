// IPv4 (RFC 791) y UDP (RFC 768) en Erlan: construcción con checksums y análisis.
// Sin fragmentación ni opciones. Los resultados del análisis quedan en globales
// del módulo (Erlan v0 no devuelve structs por referencia de forma cómoda).

extern c "wire.h" {
    fn be16_get(b: []u8, off: u64) -> u16;
    fn be16_put(b: []u8, off: u64, v: u16);
    fn be32_get(b: []u8, off: u64) -> u32;
    fn be32_put(b: []u8, off: u64, v: u32);
    fn inet_sum(b: []u8, start: u64, len: u64, seed: u32) -> u32;
    fn inet_csum(b: []u8, start: u64, len: u64, seed: u32) -> u16;
    fn mac_copy(dst: []u8, doff: u64, src: []u8, soff: u64);
}

const ETH_HDR: u64 = 14;
const IP_HDR: u64 = 20;
const UDP_HDR: u64 = 8;
const IP_OFF: u64 = 14;
const UDP_OFF: u64 = 34;
const UDP_DATA_OFF: u64 = 42;      // 14 + 20 + 8
const ETHERTYPE_IP: u16 = 0x0800;
const PROTO_ICMP: u64 = 1;
const PROTO_TCP: u64 = 6;
const PROTO_UDP: u64 = 17;

var ip_ident: u16 = 1;

// Resultado del último ip_parse
var ip_src: u32 = 0;
var ip_dst: u32 = 0;
var ip_proto: u64 = 0;
var ip_payload_off: u64 = 0;
var ip_payload_len: u64 = 0;
// Resultado del último udp_parse
var udp_sport: u16 = 0;
var udp_dport: u16 = 0;
var udp_data_off: u64 = 0;
var udp_data_len: u64 = 0;

fn ip_last_src() -> u32 { return ip_src; }
fn ip_last_proto() -> u64 { return ip_proto; }
fn udp_last_sport() -> u16 { return udp_sport; }
fn udp_last_dport() -> u16 { return udp_dport; }
fn udp_last_data_off() -> u64 { return udp_data_off; }
fn udp_last_data_len() -> u64 { return udp_data_len; }

// Analiza Ethernet+IPv4. Devuelve false si no es IPv4 válido para nosotros.
fn ip_parse(frame: []u8, len: u64, my_ip: u32) -> bool {
    if len < ETH_HDR + IP_HDR { return false; }
    if be16_get(frame, 12) != ETHERTYPE_IP { return false; }
    let ver_ihl = frame[IP_OFF];
    if (ver_ihl >> 4) != 4 { return false; }
    let ihl = ((ver_ihl & 0xF) as u64) * 4;
    if ihl < IP_HDR || len < ETH_HDR + ihl { return false; }
    if inet_csum(frame, IP_OFF, ihl, 0) != 0 { return false; }
    let total = be16_get(frame, IP_OFF + 2) as u64;
    if total < ihl || ETH_HDR + total > len { return false; }
    let flags_frag = be16_get(frame, IP_OFF + 6);
    if (flags_frag & 0x3FFF) != 0 { return false; }     // fragmentos: no soportados
    ip_src = be32_get(frame, IP_OFF + 12);
    ip_dst = be32_get(frame, IP_OFF + 16);
    if my_ip != 0 && ip_dst != my_ip && ip_dst != 0xFFFFFFFF { return false; }
    ip_proto = frame[IP_OFF + 9] as u64;
    ip_payload_off = ETH_HDR + ihl;
    ip_payload_len = total - ihl;
    return true;
}

// Requiere ip_parse previo con proto UDP.
fn udp_parse(frame: []u8, len: u64) -> bool {
    if ip_proto != PROTO_UDP || ip_payload_len < UDP_HDR { return false; }
    let o = ip_payload_off;
    udp_sport = be16_get(frame, o);
    udp_dport = be16_get(frame, o + 2);
    let ulen = be16_get(frame, o + 4) as u64;
    if ulen < UDP_HDR || ulen > ip_payload_len { return false; }
    let csum = be16_get(frame, o + 6);
    if csum != 0 {
        var pseudo: [12]u8;
        be32_put(pseudo[..], 0, ip_src);
        be32_put(pseudo[..], 4, ip_dst);
        pseudo[8] = 0;
        pseudo[9] = PROTO_UDP as u8;
        be16_put(pseudo[..], 10, ulen as u16);
        let seed = inet_sum(pseudo[..], 0, 12, 0);
        if inet_csum(frame, o, ulen, seed) != 0 { return false; }
    }
    udp_data_off = o + UDP_HDR;
    udp_data_len = ulen - UDP_HDR;
    return true;
}

// Rellena Ethernet+IPv4+UDP alrededor de un payload ya escrito en buf[UDP_DATA_OFF..].
// Devuelve la longitud total de la trama.
fn ip_udp_build(buf: []u8, dst_mac: []u8, src_mac: []u8, src_ip: u32, dst_ip: u32,
                sport: u16, dport: u16, payload_len: u64) -> u64 {
    let total = UDP_DATA_OFF + payload_len;
    if total > buf.len || total > 1514 { return 0; }
    mac_copy(buf, 0, dst_mac, 0);
    mac_copy(buf, 6, src_mac, 0);
    be16_put(buf, 12, ETHERTYPE_IP);
    buf[IP_OFF] = 0x45;
    buf[IP_OFF + 1] = 0;
    be16_put(buf, IP_OFF + 2, (IP_HDR + UDP_HDR + payload_len) as u16);
    be16_put(buf, IP_OFF + 4, ip_ident);
    ip_ident += 1;
    be16_put(buf, IP_OFF + 6, 0x4000);          // DF
    buf[IP_OFF + 8] = 64;                        // TTL
    buf[IP_OFF + 9] = PROTO_UDP as u8;
    be16_put(buf, IP_OFF + 10, 0);
    be32_put(buf, IP_OFF + 12, src_ip);
    be32_put(buf, IP_OFF + 16, dst_ip);
    be16_put(buf, IP_OFF + 10, inet_csum(buf, IP_OFF, IP_HDR, 0));

    let ulen = UDP_HDR + payload_len;
    be16_put(buf, UDP_OFF, sport);
    be16_put(buf, UDP_OFF + 2, dport);
    be16_put(buf, UDP_OFF + 4, ulen as u16);
    be16_put(buf, UDP_OFF + 6, 0);
    var pseudo: [12]u8;
    be32_put(pseudo[..], 0, src_ip);
    be32_put(pseudo[..], 4, dst_ip);
    pseudo[8] = 0;
    pseudo[9] = PROTO_UDP as u8;
    be16_put(pseudo[..], 10, ulen as u16);
    let seed = inet_sum(pseudo[..], 0, 12, 0);
    var csum = inet_csum(buf, UDP_OFF, ulen, seed);
    if csum == 0 { csum = 0xFFFF; }
    be16_put(buf, UDP_OFF + 6, csum);
    return total;
}
