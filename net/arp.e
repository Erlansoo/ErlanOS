// ARP (RFC 826) sobre Ethernet II, en Erlan. Construcción y análisis de tramas.

extern c "wire.h" {
    fn be16_get(b: []u8, off: u64) -> u16;
    fn be16_put(b: []u8, off: u64, v: u16);
    fn be32_get(b: []u8, off: u64) -> u32;
    fn be32_put(b: []u8, off: u64, v: u32);
    fn mac_copy(dst: []u8, doff: u64, src: []u8, soff: u64);
}

const ETH_HDR: u64 = 14;
const ETHERTYPE_ARP: u16 = 0x0806;
const ARP_LEN: u64 = 42;           // 14 + 28
const ARP_REQUEST: u16 = 1;
const ARP_REPLY: u16 = 2;

// Construye una trama ARP. Para request, tha = 0 y destino Ethernet = broadcast.
fn arp_build(buf: []u8, oper: u16, my_mac: []u8, my_ip: u32, target_mac: []u8, target_ip: u32) -> u64 {
    if buf.len < ARP_LEN { return 0; }
    if oper == ARP_REQUEST {
        for i in 0..6 { buf[i] = 0xFF; }
    } else {
        mac_copy(buf, 0, target_mac, 0);
    }
    mac_copy(buf, 6, my_mac, 0);
    be16_put(buf, 12, ETHERTYPE_ARP);
    be16_put(buf, 14, 1);          // htype Ethernet
    be16_put(buf, 16, 0x0800);     // ptype IPv4
    buf[18] = 6;
    buf[19] = 4;
    be16_put(buf, 20, oper);
    mac_copy(buf, 22, my_mac, 0);
    be32_put(buf, 28, my_ip);
    if oper == ARP_REQUEST {
        for i in 0..6 { buf[32 + i] = 0; }
    } else {
        mac_copy(buf, 32, target_mac, 0);
    }
    be32_put(buf, 38, target_ip);
    return ARP_LEN;
}

fn arp_is_arp(frame: []u8, len: u64) -> bool {
    if len < ARP_LEN { return false; }
    return be16_get(frame, 12) == ETHERTYPE_ARP && be16_get(frame, 16) == 0x0800 && frame[18] == 6 && frame[19] == 4;
}

fn arp_oper(frame: []u8) -> u16 { return be16_get(frame, 20); }
fn arp_sender_ip(frame: []u8) -> u32 { return be32_get(frame, 28); }
fn arp_target_ip(frame: []u8) -> u32 { return be32_get(frame, 38); }
fn arp_sender_mac(frame: []u8, out: []u8) { mac_copy(out, 0, frame, 22); }

// ¿Es una respuesta (o anuncio) que nos da la MAC de `ip`?
fn arp_is_reply_for(frame: []u8, len: u64, ip: u32) -> bool {
    if !arp_is_arp(frame, len) { return false; }
    return arp_oper(frame) == ARP_REPLY && arp_sender_ip(frame) == ip;
}

fn arp_is_request_for(frame: []u8, len: u64, my_ip: u32) -> bool {
    if !arp_is_arp(frame, len) || my_ip == 0 { return false; }
    return arp_oper(frame) == ARP_REQUEST && arp_target_ip(frame) == my_ip;
}

// Responde a una petición dirigida a nosotros. Devuelve la longitud de la respuesta.
fn arp_answer(request: []u8, out: []u8, my_mac: []u8, my_ip: u32) -> u64 {
    var requester: [6]u8;
    arp_sender_mac(request, requester[..]);
    return arp_build(out, ARP_REPLY, my_mac, my_ip, requester[..], arp_sender_ip(request));
}
