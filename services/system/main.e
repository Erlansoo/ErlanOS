// L2 trusted broker. Only the model/Mind client can send on this private endpoint.
// Policy and versioned AI messages live here, not in Ring 0.
import usys;
import ulib;
import ai_wire;

import document;
import workspace;

fn snapshot(out: []u8) {
    for i in 0..8 { ai_put64(out, 40 + i * 8, usys(14, 7, i, 0)); }
}

fn audit(id: u64, op: u64, status: u64, receipt: u64, out: []u8) {
    var b: [256]u8;
    var n = fmt_str(b[..], 0, "[m4] receipt=");
    n = fmt_u64(b[..], n, receipt);
    n = fmt_str(b[..], n, " request="); n = fmt_u64(b[..], n, id);
    n = fmt_str(b[..], n, " op="); n = fmt_u64(b[..], n, op);
    n = fmt_str(b[..], n, " status="); n = fmt_u64(b[..], n, status);
    n = fmt_str(b[..], n, " mode="); n = fmt_u64(b[..], n, ai_get64(out, 88));
    n = fmt_str(b[..], n, " revision="); n = fmt_u64(b[..], n, ai_get64(out, 96));
    ulog_bytes(b[0..n]);
}

fn main() {
    document_init();
    workspace_init();
    ulog("system: listo");
    var last: u64 = 0;
    var receipt: u64 = 0;
    loop {
        var req: [256]u8;
        let n = urecv(8, req[..]);
        if n > 256 { ulog("system: recv error"); uexit(1); }
        if n>=8 && ai_get64(req[..],0)==0x31505357 {
            if usend(9,workspace_handle(req[0..n]))!=0 { uexit(2); } continue;
        }
        if n >= 8 && ai_get64(req[..], 0) == 0x31434f44 {
            let reply = document_handle(req[0..n]);
            if usend(9, reply) != 0 { ulog("system: document send error"); uexit(2); }
            continue;
        }
        var id: u64 = 0;
        var op: u64 = 0;
        if n >= 16 { id = ai_get64(req[..], 8); }
        if n >= 24 { op = ai_get64(req[..], 16); }
        var status = ai_validate(req[0..n], last);
        receipt += 1;
        if status == 0 {
            last = id;
            var result: u64 = 0;
            if op != 1 { usys(13, 3, 3, 0); }
            if op == 2 { result = usys(15, 7, 1, 0); }
            if op == 3 { result = usys(15, 7, 0, 0); }
            if op == 4 { result = usys(16, 3, 1, receipt * 16 + 4); }
            if (op == 2 || op == 3) && result < 0xFFFFFFFFFFFFFFFE {
                result = usys(16, 3, 1, receipt * 16 + op);
            }
            if result >= 0xFFFFFFFFFFFFFFFE { status = 6; }
            if op != 1 { usys(13, 3, 0, 0); }
        }
        var out: [104]u8;
        ai_put64(out[..], 0, 1);
        ai_put64(out[..], 8, id);
        ai_put64(out[..], 16, op);
        ai_put64(out[..], 24, status);
        ai_put64(out[..], 32, receipt);
        snapshot(out[..]);
        audit(id, op, status, receipt, out[..]);
        if usend(9, out[..]) != 0 { ulog("system: send error"); uexit(2); }
    }
}
