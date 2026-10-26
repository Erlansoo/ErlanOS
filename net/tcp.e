// TCP (RFC 9293) cliente en Erlan: una conexión activa a la vez, por sondeo.
// Envío con parada y espera por segmento (MSS 1460) y RTO exponencial; recepción en
// orden a un buffer circular de 64 KiB con ACK inmediato; FIN, RST y opción MSS.
// Límites v1 declarados: sin escalado de ventana, sin SACK, descarta segmentos fuera
// de orden (el par los retransmite) y no respeta ventanas de envío menores que un MSS.

import netplat;
import wire;
import netio;
import ipv4;

const ST_CLOSED: u64 = 0;
const ST_SYN_SENT: u64 = 1;
const ST_ESTABLISHED: u64 = 2;
const ST_FIN_WAIT: u64 = 3;
const F_FIN: u64 = 1;
const F_SYN: u64 = 2;
const F_RST: u64 = 4;
const F_PSH: u64 = 8;
const F_ACK: u64 = 16;
const RBUF: u64 = 65536;
const MSS: u64 = 1460;

var st: u64 = 0;
var peer_ip: u32 = 0;
var peer_port: u16 = 0;
var local_port: u16 = 0;
var next_port: u64 = 50000;
var peer_mac: [6]u8;
var local_mac: [6]u8;
var snd_una: u32 = 0;
var snd_nxt: u32 = 0;
var rcv_nxt: u32 = 0;
var peer_mss: u64 = 536;
var peer_wnd: u64 = 0;
var peer_fin: bool = false;
var reset: bool = false;
var rbuf: [65536]u8;
var r_head: u64 = 0;
var r_count: u64 = 0;
var tx: [1600]u8;
var rx: [2048]u8;
var ident: u64 = 1;
var stat_retx: u64 = 0;
var stat_segs_in: u64 = 0;
var stat_bytes_in: u64 = 0;
var stat_dup: u64 = 0;

fn tcp_state() -> u64 { return st; }
fn tcp_retransmits() -> u64 { return stat_retx; }
fn tcp_bytes_in() -> u64 { return stat_bytes_in; }
fn tcp_segments_in() -> u64 { return stat_segs_in; }
fn tcp_discarded() -> u64 { return stat_dup; }
fn tcp_eof() -> bool { return r_count == 0 && (peer_fin || reset); }
fn tcp_was_reset() -> bool { return reset; }

fn tcp_window() -> u64 {
    let free = RBUF - r_count;
    if free > 65535 { return 65535; }
    return free;
}

// d1 = x - a en aritmética de 32 bits; cierto si a < x <= b.
fn tcp_seq_in(a: u32, x: u32, b: u32) -> bool {
    let d1 = x - a;
    let d2 = b - a;
    return d1 != 0 && d1 <= d2;
}

fn tcp_emit(flags: u64, seq: u32, data: []u8, with_mss: bool) -> bool {
    var opt: u64 = 0;
    if with_mss { opt = 4; }
    let hl: u64 = 20 + opt;
    let seglen = hl + data.len;
    let total: u64 = 34 + seglen;
    if total > 1514 { return false; }
    let ip: u64 = 14;
    let t: u64 = 34;
    let my_ip = netio_my_ip();
    mac_copy(tx[..], 0, peer_mac[..], 0);
    mac_copy(tx[..], 6, local_mac[..], 0);
    be16_put(tx[..], 12, 0x0800);
    tx[ip] = 0x45;
    tx[ip + 1] = 0;
    be16_put(tx[..], ip + 2, (20 + seglen) as u16);
    be16_put(tx[..], ip + 4, (ident & 0xFFFF) as u16);
    ident += 1;
    be16_put(tx[..], ip + 6, 0x4000);
    tx[ip + 8] = 64;
    tx[ip + 9] = 6;
    be16_put(tx[..], ip + 10, 0);
    be32_put(tx[..], ip + 12, my_ip);
    be32_put(tx[..], ip + 16, peer_ip);
    be16_put(tx[..], ip + 10, inet_csum(tx[..], ip, 20, 0));
    be16_put(tx[..], t, local_port);
    be16_put(tx[..], t + 2, peer_port);
    be32_put(tx[..], t + 4, seq);
    be32_put(tx[..], t + 8, rcv_nxt);
    tx[t + 12] = ((hl / 4) << 4) as u8;
    tx[t + 13] = flags as u8;
    be16_put(tx[..], t + 14, tcp_window() as u16);
    be16_put(tx[..], t + 16, 0);
    be16_put(tx[..], t + 18, 0);
    if with_mss {
        tx[t + 20] = 2;
        tx[t + 21] = 4;
        be16_put(tx[..], t + 22, MSS as u16);
    }
    for i in 0..data.len { tx[t + hl + i] = data[i]; }
    var pseudo: [12]u8;
    be32_put(pseudo[..], 0, my_ip);
    be32_put(pseudo[..], 4, peer_ip);
    pseudo[8] = 0;
    pseudo[9] = 6;
    be16_put(pseudo[..], 10, seglen as u16);
    let seed = inet_sum(pseudo[..], 0, 12, 0);
    be16_put(tx[..], t + 16, inet_csum(tx[..], t, seglen, seed));
    var ok = false;
    unsafe { ok = netplat_tx(&tx[0], total); }
    return ok;
}

fn tcp_ack_now() { tcp_emit(F_ACK, snd_nxt, rx[0..0], false); }

// Procesa una trama. Devuelve true si pertenecía a nuestra conexión.
fn tcp_input(frame: []u8, len: u64) -> bool {
    if !ip_parse(frame, len, netio_my_ip()) { handle_background(frame, len); return false; }
    if ip_last_proto() != 6 || ip_last_src() != peer_ip || st == ST_CLOSED { return false; }
    let ihl = ((frame[14] & 0xF) as u64) * 4;
    let ip_total = be16_get(frame, 16) as u64;
    if ip_total < ihl + 20 { return false; }
    let t = 14 + ihl;
    let seglen = ip_total - ihl;
    var pseudo: [12]u8;
    be32_put(pseudo[..], 0, peer_ip);
    be32_put(pseudo[..], 4, netio_my_ip());
    pseudo[8] = 0;
    pseudo[9] = 6;
    be16_put(pseudo[..], 10, seglen as u16);
    if inet_csum(frame, t, seglen, inet_sum(pseudo[..], 0, 12, 0)) != 0 { return false; }
    if be16_get(frame, t) != peer_port || be16_get(frame, t + 2) != local_port { return false; }
    let seq = be32_get(frame, t + 4);
    let ack = be32_get(frame, t + 8);
    let doff = ((frame[t + 12] >> 4) as u64) * 4;
    let flags = frame[t + 13] as u64;
    let wnd = be16_get(frame, t + 14) as u64;
    if doff < 20 || doff > seglen { return false; }
    let dlen = seglen - doff;
    let dstart = t + doff;
    stat_segs_in += 1;

    if (flags & F_RST) != 0 {
        reset = true;
        st = ST_CLOSED;
        netplat_log("tcp: RST recibido");
        return true;
    }
    if st == ST_SYN_SENT {
        if (flags & (F_SYN | F_ACK)) == (F_SYN | F_ACK) && ack == snd_nxt {
            rcv_nxt = seq + 1;
            snd_una = ack;
            peer_wnd = wnd;
            var o = t + 20;
            while o + 1 < t + doff {
                let kind = frame[o] as u64;
                if kind == 0 { break; }
                if kind == 1 { o += 1; continue; }
                let olen = frame[o + 1] as u64;
                if olen < 2 { break; }
                if kind == 2 && olen == 4 { peer_mss = be16_get(frame, o + 2) as u64; }
                o += olen;
            }
            st = ST_ESTABLISHED;
            tcp_ack_now();
        }
        return true;
    }
    if (flags & F_ACK) != 0 {
        if tcp_seq_in(snd_una, ack, snd_nxt) { snd_una = ack; }
        peer_wnd = wnd;
    }
    var fin = (flags & F_FIN) != 0;
    if dlen > 0 || fin {
        var skip: u64 = 0;
        var accept = false;
        let behind = (rcv_nxt - seq) as u64;
        if seq == rcv_nxt { accept = true; }
        else if behind < 0x80000000 && behind < dlen { skip = behind; accept = true; }
        if accept && !peer_fin {
            var n = dlen - skip;
            let free = RBUF - r_count;
            if n > free { n = free; fin = false; }
            for i in 0..n {
                rbuf[(r_head + r_count) % RBUF] = frame[dstart + skip + i];
                r_count += 1;
            }
            rcv_nxt = rcv_nxt + (n as u32);
            stat_bytes_in += n;
            if fin && skip + n == dlen {
                rcv_nxt = rcv_nxt + 1;
                peer_fin = true;
            }
        } else {
            stat_dup += 1;
        }
        tcp_ack_now();
    }
    return true;
}

fn tcp_poll_once() -> bool {
    var got: u64 = 0;
    unsafe { got = netplat_rx(&rx[0], 2048); }
    if got == 0 { return false; }
    tcp_input(rx[..], got);
    return true;
}

fn tcp_connect(ip: u32, port: u16, timeout_ms: u64) -> bool {
    if netio_my_ip() == 0 { netplat_log("tcp: sin direccion IP"); return false; }
    netio_my_mac(local_mac[..]);
    if !netio_arp_resolve(next_hop(ip), peer_mac[..], 3000) {
        netplat_log("tcp: sin MAC del siguiente salto");
        return false;
    }
    peer_ip = ip;
    peer_port = port;
    local_port = next_port as u16;
    next_port += 1;
    if next_port > 60999 { next_port = 50000; }
    r_head = 0;
    r_count = 0;
    peer_fin = false;
    reset = false;
    peer_mss = 536;
    let iss = ((netplat_now_ms() * 2654435761 + (local_port as u64) * 40503) & 0xFFFFFFFF) as u32;
    snd_una = iss;
    snd_nxt = iss + 1;
    rcv_nxt = 0;
    st = ST_SYN_SENT;
    let deadline = netplat_now_ms() + timeout_ms;
    var rto: u64 = 1000;
    while netplat_now_ms() < deadline {
        tcp_emit(F_SYN, iss, rx[0..0], true);
        let until = netplat_now_ms() + rto;
        while netplat_now_ms() < until && st == ST_SYN_SENT {
            if !tcp_poll_once() { netplat_wait_ms(1); }
        }
        if st == ST_ESTABLISHED { return true; }
        if reset { netplat_log("tcp: conexion rechazada (RST)"); return false; }
        stat_retx += 1;
        rto = rto * 2;
    }
    st = ST_CLOSED;
    netplat_log("tcp: sin respuesta al SYN");
    return false;
}

// Envía todo data. Cada segmento espera su ACK antes del siguiente.
fn tcp_send(data: []u8) -> bool {
    var off: u64 = 0;
    while off < data.len {
        if st != ST_ESTABLISHED { return false; }
        var mss = peer_mss;
        if mss > MSS { mss = MSS; }
        var n = data.len - off;
        if n > mss { n = mss; }
        let seq = snd_nxt;
        snd_nxt = snd_nxt + (n as u32);
        var rto: u64 = 500;
        var tries: u64 = 0;
        loop {
            tcp_emit(F_ACK | F_PSH, seq, data[off..off + n], false);
            let until = netplat_now_ms() + rto;
            while netplat_now_ms() < until && snd_una != snd_nxt && st == ST_ESTABLISHED {
                if !tcp_poll_once() { netplat_wait_ms(1); }
            }
            if snd_una == snd_nxt { break; }
            if st != ST_ESTABLISHED { return false; }
            tries += 1;
            stat_retx += 1;
            if tries > 7 { netplat_log("tcp: sin ACK, conexion perdida"); st = ST_CLOSED; return false; }
            rto = rto * 2;
            if rto > 8000 { rto = 8000; }
        }
        off += n;
    }
    return true;
}

// Copia hasta out.len bytes. Espera como mucho timeout_ms si el buffer está vacío.
// Devuelve 0 si vence el plazo o si el par cerró (distinguir con tcp_eof()).
fn tcp_recv(out: []u8, timeout_ms: u64) -> u64 {
    let deadline = netplat_now_ms() + timeout_ms;
    while r_count == 0 && !peer_fin && !reset && netplat_now_ms() < deadline {
        if !tcp_poll_once() { netplat_wait_ms(1); }
    }
    while tcp_poll_once() { }
    var n = r_count;
    if n > out.len { n = out.len; }
    let was_tight = tcp_window() < 4 * MSS;
    for i in 0..n { out[i] = rbuf[(r_head + i) % RBUF]; }
    r_head = (r_head + n) % RBUF;
    r_count -= n;
    if was_tight && n > 0 && st == ST_ESTABLISHED { tcp_ack_now(); }
    return n;
}

fn tcp_close() {
    if st == ST_ESTABLISHED {
        let seq = snd_nxt;
        snd_nxt = snd_nxt + 1;
        st = ST_FIN_WAIT;
        var tries: u64 = 0;
        while tries < 3 && snd_una != snd_nxt && !reset {
            tcp_emit(F_FIN | F_ACK, seq, rx[0..0], false);
            let until = netplat_now_ms() + 500;
            while netplat_now_ms() < until && snd_una != snd_nxt {
                if !tcp_poll_once() { netplat_wait_ms(1); }
            }
            tries += 1;
        }
    }
    st = ST_CLOSED;
}
