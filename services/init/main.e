// init: primer servicio de usuario de ErlanOS (L2). Demuestra Ring 3, capabilities,
// estado SSE preservado entre cambios de contexto e IPC hacia `echo`.

import usys;
import ulib;

fn main() {
    ulog("hola desde Ring 3");
    ulog_num("pid=", ugetpid());
    ulog_num("uptime_ms=", uuptime_ms());

    // Estado de punto flotante a través de muchos cambios de contexto.
    var acc: f64 = 0.0;
    for i in 0..2000 {
        acc += 0.25;
        if i % 50 == 0 { uyield(); }
    }
    ulog_num("fpu: acc*100 = ", (acc * 100.0) as u64);   // esperado 50000
    if (acc * 100.0) as u64 != 50000 { ulog("FPU CORRUPTA"); uexit(2); }

    // Capability inexistente: el kernel debe denegar sin efecto.
    var msg: [8]u8;
    msg[0] = 120;
    let r = usys(1, 5, 0, 0);
    ulog_num("log con slot 5 devolvio ", r);

    // M4: an inspect-only capability never permits state changes.
    let mode_before = usys(14, 7, 6, 0);
    if mode_before > 1 || usys(15, 7, 1, 0) != 0xFFFFFFFFFFFFFFFF {
        ulog("m4: readonly FAIL"); uexit(3);
    }
    if usys(14, 7, 6, 0) != mode_before { ulog("m4: readonly FAIL"); uexit(3); }
    ulog("m4: readonly PASS");

    // IPC: cinco pings a echo por el endpoint del slot 2.
    for k in 1..=5 {
        var buf: [64]u8;
        var p = fmt_str(buf[..], 0, "ping ");
        p = fmt_u64(buf[..], p, k);
        usend(2, buf[0..p]);
        usleep_ms(300);
    }
    ulog("listo, saliendo");
    uexit(0);
}
