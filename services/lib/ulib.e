// Biblioteca mínima de usuario para servicios de ErlanOS (L2), en Erlan.

import usys;

const SYS_EXIT: u64 = 0;
const SYS_LOG: u64 = 1;
const SYS_YIELD: u64 = 2;
const SYS_GETPID: u64 = 3;
const SYS_SLEEP_MS: u64 = 4;
const SYS_SEND: u64 = 5;
const SYS_RECV: u64 = 6;
const SYS_UPTIME_MS: u64 = 7;

const HALO_IDLE: u64 = 0;
const HALO_LISTENING: u64 = 1;
const HALO_LOCAL: u64 = 2;
const HALO_ACTING: u64 = 3;
const HALO_AUTHORIZATION: u64 = 4;
const HALO_CLOUD: u64 = 5;

// Slots de capabilities que el kernel concede a los servicios de M2.
const CAP_LOG_SLOT: u64 = 0;
const CAP_TIME_SLOT: u64 = 1;
const CAP_EP_SLOT: u64 = 2;

fn ustrlen(s: str) -> u64 {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } }
    return n;
}

fn uexit(code: u64) { usys(SYS_EXIT, code, 0, 0); loop {} }
fn uyield() { usys(SYS_YIELD, 0, 0, 0); }
fn usleep_ms(ms: u64) { usys(SYS_SLEEP_MS, ms, 0, 0); }
fn ugetpid() -> u64 { return usys(SYS_GETPID, 0, 0, 0); }
fn uuptime_ms() -> u64 { return usys(SYS_UPTIME_MS, CAP_TIME_SLOT, 0, 0); }

fn ulog(s: str) -> u64 {
    var p: u64 = 0;
    unsafe { p = s as u64; }
    return usys(SYS_LOG, CAP_LOG_SLOT, p, ustrlen(s));
}

fn ulog_bytes(b: []u8) -> u64 {
    var p: u64 = 0;
    unsafe { p = b.ptr as u64; }
    return usys(SYS_LOG, CAP_LOG_SLOT, p, b.len);
}

// Escribe `v` en decimal en buf a partir de pos; devuelve la nueva pos.
fn fmt_u64(buf: []u8, pos: u64, v: u64) -> u64 {
    var tmp: [24]u8;
    var n: u64 = 0;
    var x = v;
    if x == 0 { tmp[0] = 48; n = 1; }
    while x > 0 { tmp[n] = 48 + ((x % 10) as u8); x = x / 10; n += 1; }
    var p = pos;
    while n > 0 { n -= 1; buf[p] = tmp[n]; p += 1; }
    return p;
}

fn fmt_str(buf: []u8, pos: u64, s: str) -> u64 {
    var p = pos;
    let n = ustrlen(s);
    unsafe { for i in 0..n { buf[p] = s[i]; p += 1; } }
    return p;
}

// "etiqueta<valor>" en una sola línea de log.
fn ulog_num(label: str, v: u64) {
    var buf: [128]u8;
    var p = fmt_str(buf[..], 0, label);
    p = fmt_u64(buf[..], p, v);
    ulog_bytes(buf[0..p]);
}

fn usend(slot: u64, b: []u8) -> u64 {
    var p: u64 = 0;
    unsafe { p = b.ptr as u64; }
    return usys(SYS_SEND, slot, p, b.len);
}

fn urecv(slot: u64, b: []u8) -> u64 {
    var p: u64 = 0;
    unsafe { p = b.ptr as u64; }
    return usys(SYS_RECV, slot, p, b.len);
}
