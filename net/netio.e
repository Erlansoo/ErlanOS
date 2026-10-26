// Motor de E/S de la pila: buffers, caché ARP, resolución, envío UDP y espera de
// datagramas con sondeo. Responde ARP dirigido a nosotros mientras espera.
// Depende de la plataforma sólo a través de netplat.h (tx/rx/reloj/log).

import netplat;
import wire;
import arp;
import ipv4;

const UDP_DATA_OFF: u64 = 42;
const ARP_CACHE: u64 = 8;
const IP_BROADCAST: u32 = 0xFFFFFFFF;

var my_mac: [6]u8;
var my_ip: u32 = 0;
var my_mask: u32 = 0;
var my_gw: u32 = 0;
var txbuf: [2048]u8;
var rxbuf: [2048]u8;
var cache_ip: [8]u32;
var cache_mac: [48]u8;
var cache_n: u64 = 0;
var last_src_ip: u32 = 0;
var last_sport: u16 = 0;
var frames_seen: u64 = 0;
var arp_answered: u64 = 0;

fn netio_init() {
    unsafe { netplat_mac(&my_mac[0]); }
    my_ip = 0; my_mask = 0; my_gw = 0; cache_n = 0;
}

fn netio_set_addr(ip: u32, mask: u32, gw: u32) { my_ip = ip; my_mask = mask; my_gw = gw; }
fn netio_my_ip() -> u32 { return my_ip; }
fn netio_my_mac(out: []u8) { mac_copy(out, 0, my_mac[..], 0); }
fn netio_last_src_ip() -> u32 { return last_src_ip; }
fn netio_last_sport() -> u16 { return last_sport; }
fn netio_frames_seen() -> u64 { return frames_seen; }
// Payload UDP de salida: los protocolos escriben aquí antes de netio_send_udp.
fn netio_payload() -> []u8 { return txbuf[42..1514]; }
fn netio_rxbuf() -> []u8 { return rxbuf[..]; }

fn cache_find(ip: u32, out: []u8) -> bool {
    for i in 0..cache_n {
        if cache_ip[i] == ip { mac_copy(out, 0, cache_mac[..], i * 6); return true; }
    }
    return false;
}

fn cache_put(ip: u32, m: []u8) {
    for i in 0..cache_n {
        if cache_ip[i] == ip { mac_copy(cache_mac[..], i * 6, m, 0); return; }
    }
    var slot = cache_n;
    if slot >= ARP_CACHE { slot = ARP_CACHE - 1; } else { cache_n += 1; }
    cache_ip[slot] = ip;
    mac_copy(cache_mac[..], slot * 6, m, 0);
}

fn netio_send(len: u64) -> bool {
    var ok = false;
    unsafe { ok = netplat_tx(&txbuf[0], len); }
    return ok;
}

// Procesa una trama recibida que no era lo que esperábamos: ARP entrante.
fn handle_background(frame: []u8, len: u64) {
    if !arp_is_arp(frame, len) { return; }
    var m: [6]u8;
    arp_sender_mac(frame, m[..]);
    cache_put(arp_sender_ip(frame), m[..]);
    if arp_is_request_for(frame, len, my_ip) {
        let n = arp_answer(frame, txbuf[..], my_mac[..], my_ip);
        if n > 0 && netio_send(n) { arp_answered += 1; }
    }
}

// Resuelve la MAC de una IP del segmento (SLIRP responde por 10.0.2.2, 10.0.2.3...).
fn netio_arp_resolve(ip: u32, out: []u8, timeout_ms: u64) -> bool {
    if ip == IP_BROADCAST { for i in 0..6 { out[i] = 0xFF; } return true; }
    if cache_find(ip, out) { return true; }
    let deadline = netplat_now_ms() + timeout_ms;
    var next_send: u64 = 0;
    var zero_mac: [6]u8;
    while netplat_now_ms() < deadline {
        let now = netplat_now_ms();
        if now >= next_send {
            let n = arp_build(txbuf[..], 1, my_mac[..], my_ip, zero_mac[..], ip);
            netio_send(n);
            next_send = now + 500;
        }
        var got: u64 = 0;
        unsafe { got = netplat_rx(&rxbuf[0], 2048); }
        if got == 0 { netplat_wait_ms(1); continue; }
        frames_seen += 1;
        if arp_is_reply_for(rxbuf[..], got, ip) {
            arp_sender_mac(rxbuf[..], out);
            cache_put(ip, out);
            return true;
        }
        handle_background(rxbuf[..], got);
    }
    return false;
}

// Siguiente salto: destino en nuestra subred (o sin dirección aún) -> directo; si no, gateway.
fn next_hop(dst_ip: u32) -> u32 {
    if dst_ip == IP_BROADCAST || my_ip == 0 || my_mask == 0 || my_gw == 0 { return dst_ip; }
    if (dst_ip & my_mask) == (my_ip & my_mask) { return dst_ip; }
    return my_gw;
}

// Envía el payload ya escrito en netio_payload() como UDP src_ip:sport -> dst_ip:dport.
fn netio_send_udp(src_ip: u32, dst_ip: u32, sport: u16, dport: u16, payload_len: u64) -> bool {
    var dst_mac: [6]u8;
    if !netio_arp_resolve(next_hop(dst_ip), dst_mac[..], 3000) { return false; }
    // netio_arp_resolve pudo usar txbuf; reconstruir cabeceras sobre el payload intacto.
    let n = ip_udp_build(txbuf[..], dst_mac[..], my_mac[..], src_ip, dst_ip, sport, dport, payload_len);
    if n == 0 { return false; }
    return netio_send(n);
}

// Espera un datagrama UDP dirigido al puerto `port`. Copia el payload en out y
// devuelve su longitud (0 si se agotó el tiempo). Atiende ARP mientras tanto.
fn netio_wait_udp(port: u16, timeout_ms: u64, out: []u8) -> u64 {
    let deadline = netplat_now_ms() + timeout_ms;
    while netplat_now_ms() < deadline {
        var got: u64 = 0;
        unsafe { got = netplat_rx(&rxbuf[0], 2048); }
        if got == 0 { netplat_wait_ms(1); continue; }
        frames_seen += 1;
        if arp_is_arp(rxbuf[..], got) { handle_background(rxbuf[..], got); continue; }
        if !ip_parse(rxbuf[..], got, my_ip) { continue; }
        if ip_last_proto() != 17 { continue; }
        if !udp_parse(rxbuf[..], got) { continue; }
        if udp_last_dport() != port { continue; }
        var n = udp_last_data_len();
        if n > out.len { n = out.len; }
        let off = udp_last_data_off();
        for i in 0..n { out[i] = rxbuf[off + i]; }
        last_src_ip = ip_last_src();
        last_sport = udp_last_sport();
        return n;
    }
    return 0;
}

fn netio_log_ip(label: str, ip: u32) {
    var line: [96]u8;
    var p = fmt_str(line[..], 0, label);
    p = fmt_ip(line[..], p, ip);
    line[p] = 0;
    unsafe { netplat_log(&line[0] as str); }
}
