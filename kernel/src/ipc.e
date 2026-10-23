// L1 — IPC síncrono por endpoints: un slot de mensaje de hasta 256 bytes.
// El emisor bloquea si el slot está lleno; el receptor bloquea si está vacío.

extern c "kmem.h" { fn kmemcpy(dst: u64, src: u64, n: u64); }
extern c "sched.h" {
    fn sched_current_id() -> u64;
    fn sched_block(obj: u64);
    fn sched_wake(id: u64);
}
extern c "serial.h" { fn kprintf(fmt: str, ...); }
extern c "panic.h" { fn kpanic(msg: str); }

const MAX_ENDPOINTS: u64 = 8;
const MSG_MAX: u64 = 256;

struct Endpoint {
    used: bool;
    has_msg: bool;
    len: u64;
    sender: u64;
    recv_waiter: u64;    // id+1 del hilo bloqueado en recv, 0 = nadie
    send_waiter: u64;    // id+1 del hilo bloqueado en send
    buf: [256]u8;
}

var endpoints: [8]Endpoint;

fn ipc_init() {
    for i in 0..MAX_ENDPOINTS { endpoints[i].used = false; endpoints[i].has_msg = false; }
}

fn ipc_create() -> u64 {
    for i in 0..MAX_ENDPOINTS {
        if !endpoints[i].used {
            endpoints[i].used = true;
            endpoints[i].has_msg = false;
            endpoints[i].recv_waiter = 0;
            endpoints[i].send_waiter = 0;
            return i;
        }
    }
    kpanic("ipc: sin endpoints");
    return 0;
}

// Copia `len` bytes desde `src` (memoria del emisor, espacio actual) al endpoint.
fn ipc_send(ep: u64, src: u64, len: u64) -> u64 {
    if ep >= MAX_ENDPOINTS || !endpoints[ep].used { return 1; }
    if len > MSG_MAX { return 2; }
    let e = &endpoints[ep];
    while e.has_msg {
        e.send_waiter = sched_current_id() + 1;
        sched_block(ep);
    }
    var dst: u64 = 0;
    unsafe { dst = &e.buf[0] as u64; }
    kmemcpy(dst, src, len);
    e.len = len;
    e.sender = sched_current_id();
    e.has_msg = true;
    if e.recv_waiter != 0 { let w = e.recv_waiter - 1; e.recv_waiter = 0; sched_wake(w); }
    return 0;
}

// Espera un mensaje y lo copia a `dst` (máximo `cap` bytes). Devuelve la longitud.
fn ipc_recv(ep: u64, dst: u64, cap: u64) -> u64 {
    if ep >= MAX_ENDPOINTS || !endpoints[ep].used { return 0; }
    let e = &endpoints[ep];
    while !e.has_msg {
        e.recv_waiter = sched_current_id() + 1;
        sched_block(ep);
    }
    var n = e.len;
    if n > cap { n = cap; }
    var src: u64 = 0;
    unsafe { src = &e.buf[0] as u64; }
    kmemcpy(dst, src, n);
    e.has_msg = false;
    if e.send_waiter != 0 { let w = e.send_waiter - 1; e.send_waiter = 0; sched_wake(w); }
    return n;
}
