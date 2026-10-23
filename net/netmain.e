// Orquestador de la prueba de red: ARP al gateway -> DHCP -> ARP al DNS -> consulta DNS
// de un nombre público -> TCP masivo contra el anfitrión (con SHA-256 nativo) -> HTTP
// real a example.com. Cada etapa deja un marcador verificable en el log serial y una
// línea en la consola. Es la demostración de "ErlanOS en internet".

extern c "netplat.h" { fn netplat_log(s: str); fn netplat_console(p: ptr<u8>, n: u64); fn netplat_now_ms() -> u64; }
extern c "wire.h" {
    fn fmt_str(buf: []u8, pos: u64, s: str) -> u64;
    fn fmt_u64(buf: []u8, pos: u64, v: u64) -> u64;
    fn fmt_ip(buf: []u8, pos: u64, ip: u32) -> u64;
    fn fmt_mac(buf: []u8, pos: u64, m: []u8, off: u64) -> u64;
    fn fmt_hex8(buf: []u8, pos: u64, v: u8) -> u64;
    fn ip_make(a: u64, b: u64, c: u64, d: u64) -> u32;
}
extern c "netio.h" {
    fn netio_init();
    fn netio_my_mac(out: []u8);
    fn netio_my_ip() -> u32;
    fn netio_arp_resolve(ip: u32, out: []u8, timeout_ms: u64) -> bool;
    fn netio_frames_seen() -> u64;
}
extern c "dhcp.h" {
    fn dhcp_run(timeout_ms: u64) -> bool;
    fn dhcp_ip() -> u32;
    fn dhcp_mask() -> u32;
    fn dhcp_gw() -> u32;
    fn dhcp_dns() -> u32;
    fn dhcp_lease_secs() -> u32;
}
extern c "dns.h" { fn dns_resolve(name: str, server: u32, timeout_ms: u64) -> u32; }
extern c "tcp.h" {
    fn tcp_connect(ip: u32, port: u16, timeout_ms: u64) -> bool;
    fn tcp_send(data: []u8) -> bool;
    fn tcp_recv(out: []u8, timeout_ms: u64) -> u64;
    fn tcp_eof() -> bool;
    fn tcp_close();
    fn tcp_retransmits() -> u64;
    fn tcp_discarded() -> u64;
}
extern c "sha2.h" {
    struct Hash { kind: u64; h32: [8]u32; w32: [64]u32; h64: [8]u64; w64: [80]u64; buf: [128]u8; n: u64; total: u64; }
    fn hash_init(c: ptr<Hash>, kind: u64);
    fn hash_update(c: ptr<Hash>, data: []u8);
    fn hash_final(c: ptr<Hash>, out: []u8);
}

const BULK_PORT: u16 = 18765;
const BULK_UP: u64 = 6000;
const BULK_DOWN: u64 = 300000;

var line: [256]u8;
var stage_ok: [6]bool;
var resolved_ip: u32 = 0;
var bulk_hash: Hash;
var io: [4096]u8;
var upload: [6000]u8;
var request: [256]u8;
var status_line: [96]u8;

fn emit(p: u64) {
    line[p] = 0;
    unsafe { netplat_log(&line[0] as str); }
    line[p] = 10;
    unsafe { netplat_console(&line[0], p + 1); }
}

fn net_probe_ok(stage: u64) -> bool { if stage < 6 { return stage_ok[stage]; } return false; }
fn net_probe_resolved() -> u32 { return resolved_ip; }

// 5. TCP contra el anfitrión (10.0.2.2 en SLIRP): subida de 6000 bytes con patrón fijo
// y bajada de 300000 bytes cuyo SHA-256 se calcula aquí, en el kernel, con sha2.e.
fn stage_tcp_bulk() -> bool {
    let host = ip_make(10, 0, 2, 2);
    let t0 = netplat_now_ms();
    if !tcp_connect(host, BULK_PORT, 8000) {
        emit(fmt_str(line[..], 0, "FALLO tcp: sin conexion con 10.0.2.2:18765"));
        return false;
    }
    for i in 0..BULK_UP { upload[i] = ((i * 7 + 13) & 0xFF) as u8; }
    if !tcp_send(upload[..]) {
        emit(fmt_str(line[..], 0, "FALLO tcp: la subida no fue confirmada"));
        tcp_close();
        return false;
    }
    hash_init(&bulk_hash, 0);
    var total: u64 = 0;
    let deadline = netplat_now_ms() + 120000;
    while !tcp_eof() && netplat_now_ms() < deadline {
        let n = tcp_recv(io[..], 2000);
        if n > 0 {
            hash_update(&bulk_hash, io[0..n]);
            total += n;
        }
    }
    tcp_close();
    var digest: [32]u8;
    hash_final(&bulk_hash, digest[..]);
    var p = fmt_str(line[..], 0, "tcp bulk ok subida=");
    if total != BULK_DOWN { p = fmt_str(line[..], 0, "FALLO tcp bulk subida="); }
    p = fmt_u64(line[..], p, BULK_UP);
    p = fmt_str(line[..], p, " bajada=");
    p = fmt_u64(line[..], p, total);
    p = fmt_str(line[..], p, " sha256=");
    for i in 0..32 { p = fmt_hex8(line[..], p, digest[i]); }
    p = fmt_str(line[..], p, " retrans=");
    p = fmt_u64(line[..], p, tcp_retransmits());
    p = fmt_str(line[..], p, " descartados=");
    p = fmt_u64(line[..], p, tcp_discarded());
    p = fmt_str(line[..], p, " en ");
    p = fmt_u64(line[..], p, netplat_now_ms() - t0);
    p = fmt_str(line[..], p, "ms");
    emit(p);
    return total == BULK_DOWN;
}

// 6. HTTP/1.1 real a example.com:80 por el NAT de QEMU hacia internet.
fn stage_http(ip: u32) -> bool {
    if ip == 0 { return false; }
    let t0 = netplat_now_ms();
    if !tcp_connect(ip, 80, 10000) {
        emit(fmt_str(line[..], 0, "FALLO http: sin conexion con example.com:80"));
        return false;
    }
    let n = fmt_str(request[..], 0, "GET / HTTP/1.1\r\nHost: example.com\r\nUser-Agent: ErlanOS/0.1\r\nAccept: */*\r\nConnection: close\r\n\r\n");
    if !tcp_send(request[0..n]) {
        emit(fmt_str(line[..], 0, "FALLO http: la peticion no fue confirmada"));
        tcp_close();
        return false;
    }
    var total: u64 = 0;
    var sl: u64 = 0;
    var head_done = false;
    let deadline = netplat_now_ms() + 30000;
    while !tcp_eof() && netplat_now_ms() < deadline {
        let got = tcp_recv(io[..], 2000);
        for i in 0..got {
            let c = io[i];
            if !head_done {
                if c == 13 || c == 10 { head_done = true; }
                else if sl < 95 { status_line[sl] = c; sl += 1; }
            }
        }
        total += got;
    }
    tcp_close();
    if sl == 0 {
        emit(fmt_str(line[..], 0, "FALLO http: sin respuesta"));
        return false;
    }
    var p = fmt_str(line[..], 0, "http ok bytes=");
    p = fmt_u64(line[..], p, total);
    p = fmt_str(line[..], p, " en ");
    p = fmt_u64(line[..], p, netplat_now_ms() - t0);
    p = fmt_str(line[..], p, "ms estado=");
    for i in 0..sl { line[p] = status_line[i]; p += 1; }
    emit(p);
    return true;
}

fn net_probe_run() {
    for i in 0..6 { stage_ok[i] = false; }
    netio_init();
    var mac: [6]u8;
    netio_my_mac(mac[..]);
    var p = fmt_str(line[..], 0, "mac ");
    p = fmt_mac(line[..], p, mac[..], 0);
    emit(p);

    // 1. ARP al gateway de SLIRP (sin dirección propia aún: sender 0.0.0.0, luego 10.0.2.15).
    let gw_guess = ip_make(10, 0, 2, 2);
    var gw_mac: [6]u8;
    var arp_ok = netio_arp_resolve(gw_guess, gw_mac[..], 3000);
    if arp_ok {
        stage_ok[0] = true;
        p = fmt_str(line[..], 0, "arp ok gw=");
        p = fmt_ip(line[..], p, gw_guess);
        p = fmt_str(line[..], p, " mac=");
        p = fmt_mac(line[..], p, gw_mac[..], 0);
        emit(p);
    } else {
        p = fmt_str(line[..], 0, "FALLO arp: sin respuesta del gateway");
        emit(p);
    }

    // 2. DHCP
    let t0 = netplat_now_ms();
    if dhcp_run(3000) {
        stage_ok[1] = true;
        p = fmt_str(line[..], 0, "dhcp ok ip=");
        p = fmt_ip(line[..], p, dhcp_ip());
        p = fmt_str(line[..], p, " mask=");
        p = fmt_ip(line[..], p, dhcp_mask());
        p = fmt_str(line[..], p, " gw=");
        p = fmt_ip(line[..], p, dhcp_gw());
        p = fmt_str(line[..], p, " dns=");
        p = fmt_ip(line[..], p, dhcp_dns());
        p = fmt_str(line[..], p, " lease=");
        p = fmt_u64(line[..], p, dhcp_lease_secs() as u64);
        p = fmt_str(line[..], p, "s en ");
        p = fmt_u64(line[..], p, netplat_now_ms() - t0);
        p = fmt_str(line[..], p, "ms");
        emit(p);
    } else {
        p = fmt_str(line[..], 0, "FALLO dhcp");
        emit(p);
    }

    // 3. ARP al servidor DNS con nuestra dirección ya asignada.
    var dns_ip = dhcp_dns();
    if dns_ip == 0 { dns_ip = ip_make(10, 0, 2, 3); }
    var dns_mac: [6]u8;
    if netio_arp_resolve(dns_ip, dns_mac[..], 3000) {
        stage_ok[2] = true;
        p = fmt_str(line[..], 0, "arp ok dns=");
        p = fmt_ip(line[..], p, dns_ip);
        p = fmt_str(line[..], p, " mac=");
        p = fmt_mac(line[..], p, dns_mac[..], 0);
        emit(p);
    } else {
        p = fmt_str(line[..], 0, "FALLO arp dns");
        emit(p);
    }

    // 4. DNS: un nombre público. La respuesta la produce el resolver real del host.
    let t1 = netplat_now_ms();
    resolved_ip = dns_resolve("example.com", dns_ip, 4000);
    if resolved_ip != 0 {
        stage_ok[3] = true;
        p = fmt_str(line[..], 0, "dns ok example.com=");
        p = fmt_ip(line[..], p, resolved_ip);
        p = fmt_str(line[..], p, " en ");
        p = fmt_u64(line[..], p, netplat_now_ms() - t1);
        p = fmt_str(line[..], p, "ms");
        emit(p);
    } else {
        p = fmt_str(line[..], 0, "FALLO dns example.com");
        emit(p);
    }

    p = fmt_str(line[..], 0, "tramas recibidas=");
    p = fmt_u64(line[..], p, netio_frames_seen());
    emit(p);
    if stage_ok[0] && stage_ok[1] && stage_ok[2] && stage_ok[3] {
        p = fmt_str(line[..], 0, "INTERNET OK: ErlanOS obtuvo direccion por DHCP y resolvio un nombre real");
    } else {
        p = fmt_str(line[..], 0, "prueba de red incompleta (ver etapas)");
    }
    emit(p);

    // 5 y 6: transporte fiable propio (TCP) sobre la misma pila.
    if stage_ok[1] {
        stage_ok[4] = stage_tcp_bulk();
        stage_ok[5] = stage_http(resolved_ip);
    }
}
