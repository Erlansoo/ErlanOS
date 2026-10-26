// Cliente DHCP (RFC 2131) en Erlan: DISCOVER -> OFFER -> REQUEST -> ACK sobre UDP 68/67.

import netplat;
import wire;
import netio;

const DHCP_CLIENT: u16 = 68;
const DHCP_SERVER: u16 = 67;
const OPT_MASK: u8 = 1;
const OPT_ROUTER: u8 = 3;
const OPT_DNS: u8 = 6;
const OPT_REQ_IP: u8 = 50;
const OPT_LEASE: u8 = 51;
const OPT_MSGTYPE: u8 = 53;
const OPT_SERVER: u8 = 54;
const OPT_PARAMS: u8 = 55;
const OPT_END: u8 = 255;
const MSG_DISCOVER: u8 = 1;
const MSG_OFFER: u8 = 2;
const MSG_REQUEST: u8 = 3;
const MSG_ACK: u8 = 5;
const MSG_NAK: u8 = 6;
const BOOTP_FIXED: u64 = 236;
const MIN_PAYLOAD: u64 = 300;

var xid: u32 = 0;
var lease_ip: u32 = 0;
var lease_mask: u32 = 0;
var lease_gw: u32 = 0;
var lease_dns: u32 = 0;
var lease_server: u32 = 0;
var lease_secs: u32 = 0;
var reply: [576]u8;

fn dhcp_ip() -> u32 { return lease_ip; }
fn dhcp_mask() -> u32 { return lease_mask; }
fn dhcp_gw() -> u32 { return lease_gw; }
fn dhcp_dns() -> u32 { return lease_dns; }
fn dhcp_server() -> u32 { return lease_server; }
fn dhcp_lease_secs() -> u32 { return lease_secs; }

fn build(msgtype: u8, req_ip: u32, server: u32) -> u64 {
    let p = netio_payload();
    for i in 0..MIN_PAYLOAD { p[i] = 0; }
    p[0] = 1;            // BOOTREQUEST
    p[1] = 1;            // Ethernet
    p[2] = 6;
    p[3] = 0;
    be32_put(p, 4, xid);
    be16_put(p, 8, 0);
    be16_put(p, 10, 0x8000);   // broadcast: aún no tenemos IP
    var mac: [6]u8;
    netio_my_mac(mac[..]);
    for i in 0..6 { p[28 + i] = mac[i]; }
    var o = BOOTP_FIXED;
    p[o] = 0x63; p[o + 1] = 0x82; p[o + 2] = 0x53; p[o + 3] = 0x63;
    o += 4;
    p[o] = OPT_MSGTYPE; p[o + 1] = 1; p[o + 2] = msgtype; o += 3;
    if msgtype == MSG_REQUEST {
        p[o] = OPT_REQ_IP; p[o + 1] = 4; be32_put(p, o + 2, req_ip); o += 6;
        p[o] = OPT_SERVER; p[o + 1] = 4; be32_put(p, o + 2, server); o += 6;
    }
    p[o] = OPT_PARAMS; p[o + 1] = 4;
    p[o + 2] = OPT_MASK; p[o + 3] = OPT_ROUTER; p[o + 4] = OPT_DNS; p[o + 5] = OPT_LEASE;
    o += 6;
    p[o] = OPT_END; o += 1;
    if o < MIN_PAYLOAD { o = MIN_PAYLOAD; }
    return o;
}

// Analiza una respuesta del servidor. Devuelve el tipo de mensaje (0 si no es para nosotros).
fn parse(msg: []u8, len: u64) -> u8 {
    if len < BOOTP_FIXED + 4 { return 0; }
    if msg[0] != 2 { return 0; }
    if be32_get(msg, 4) != xid { return 0; }
    var mac: [6]u8;
    netio_my_mac(mac[..]);
    for i in 0..6 { if msg[28 + i] != mac[i] { return 0; } }
    if msg[236] != 0x63 || msg[237] != 0x82 || msg[238] != 0x53 || msg[239] != 0x63 { return 0; }
    let yiaddr = be32_get(msg, 16);
    var msgtype: u8 = 0;
    var mask: u32 = 0;
    var gw: u32 = 0;
    var dns: u32 = 0;
    var server: u32 = 0;
    var lease: u32 = 0;
    var o = BOOTP_FIXED + 4;
    while o + 1 < len {
        let code = msg[o];
        if code == OPT_END { break; }
        if code == 0 { o += 1; continue; }
        let olen = msg[o + 1] as u64;
        if o + 2 + olen > len { break; }
        if code == OPT_MSGTYPE && olen >= 1 { msgtype = msg[o + 2]; }
        if code == OPT_MASK && olen == 4 { mask = be32_get(msg, o + 2); }
        if code == OPT_ROUTER && olen >= 4 { gw = be32_get(msg, o + 2); }
        if code == OPT_DNS && olen >= 4 { dns = be32_get(msg, o + 2); }
        if code == OPT_SERVER && olen == 4 { server = be32_get(msg, o + 2); }
        if code == OPT_LEASE && olen == 4 { lease = be32_get(msg, o + 2); }
        o += 2 + olen;
    }
    if msgtype == MSG_OFFER || msgtype == MSG_ACK {
        lease_ip = yiaddr;
        if mask != 0 { lease_mask = mask; }
        if gw != 0 { lease_gw = gw; }
        if dns != 0 { lease_dns = dns; }
        if server != 0 { lease_server = server; }
        if lease != 0 { lease_secs = lease; }
    }
    return msgtype;
}

// Ejecuta DORA con reintentos. Al éxito configura netio con la dirección obtenida.
fn dhcp_run(timeout_ms: u64) -> bool {
    xid = ((netplat_now_ms() * 2654435761) & 0xFFFFFFFF) as u32;
    if xid == 0 { xid = 0x45524C4E; }
    lease_ip = 0; lease_mask = 0; lease_gw = 0; lease_dns = 0; lease_server = 0; lease_secs = 0;
    var attempt: u64 = 0;
    while attempt < 4 {
        attempt += 1;
        let n = build(MSG_DISCOVER, 0, 0);
        if !netio_send_udp(0, 0xFFFFFFFF, DHCP_CLIENT, DHCP_SERVER, n) { netplat_log("dhcp: no se pudo enviar DISCOVER"); return false; }
        let deadline = netplat_now_ms() + timeout_ms;
        var offered = false;
        while netplat_now_ms() < deadline && !offered {
            let got = netio_wait_udp(DHCP_CLIENT, timeout_ms, reply[..]);
            if got == 0 { break; }
            if parse(reply[..], got) == MSG_OFFER { offered = true; }
        }
        if !offered { netplat_log("dhcp: sin OFFER, reintento"); continue; }

        let m = build(MSG_REQUEST, lease_ip, lease_server);
        if !netio_send_udp(0, 0xFFFFFFFF, DHCP_CLIENT, DHCP_SERVER, m) { return false; }
        let deadline2 = netplat_now_ms() + timeout_ms;
        while netplat_now_ms() < deadline2 {
            let got = netio_wait_udp(DHCP_CLIENT, timeout_ms, reply[..]);
            if got == 0 { break; }
            let t = parse(reply[..], got);
            if t == MSG_ACK {
                netio_set_addr(lease_ip, lease_mask, lease_gw);
                return true;
            }
            if t == MSG_NAK { netplat_log("dhcp: NAK"); break; }
        }
        netplat_log("dhcp: sin ACK, reintento");
    }
    return false;
}
