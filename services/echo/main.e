// echo: segundo servicio de usuario (L2). Recibe mensajes por IPC y los registra.
// También mantiene su propio estado SSE para detectar corrupción en el cambio de contexto.

extern c "ulib.h" {
    fn ulog(s: str) -> u64;
    fn ulog_num(label: str, v: u64);
    fn ulog_bytes(b: []u8) -> u64;
    fn fmt_str(buf: []u8, pos: u64, s: str) -> u64;
    fn uexit(code: u64);
    fn uyield();
    fn urecv(slot: u64, b: []u8) -> u64;
}

fn main() {
    ulog("echo listo, esperando mensajes");
    var acc: f64 = 0.0;
    var recibidos: u64 = 0;
    while recibidos < 5 {
        var buf: [256]u8;
        let n = urecv(2, buf[..]);
        recibidos += 1;
        var out: [300]u8;
        var p = fmt_str(out[..], 0, "recibido: ");
        for i in 0..n { out[p] = buf[i]; p += 1; }
        ulog_bytes(out[0..p]);
        for i in 0..400 { acc += 0.5; if i % 40 == 0 { uyield(); } }
    }
    ulog_num("fpu: acc = ", acc as u64);   // esperado 1000
    if acc as u64 != 1000 { ulog("FPU CORRUPTA"); uexit(2); }
    ulog("cinco mensajes recibidos, saliendo");
    uexit(0);
}
