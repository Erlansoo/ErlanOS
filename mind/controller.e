// L3 prototype, linked into the model process. Only structured IPC can operate L2.
extern c "platform.h" {
    fn platform_write(p: ptr<u8>, n: u64);
    fn platform_halo(state: u64);
    fn platform_cancelled() -> bool;
}
extern c "usys.h" { fn usys(nr: u64, a: u64, b: u64, c: u64) -> u64; }
extern c "ulib.h" {
    fn ustrlen(s: str) -> u64;
    fn ulog(s: str) -> u64;
    fn ulog_num(s: str, v: u64);
    fn fmt_str(b: []u8, p: u64, s: str) -> u64;
    fn fmt_u64(b: []u8, p: u64, v: u64) -> u64;
    fn usend(slot: u64, b: []u8) -> u64;
    fn urecv(slot: u64, b: []u8) -> u64;
}
extern c "ai_wire.h" {
    fn ai_get64(b: []u8, off: u64) -> u64;
    fn ai_put64(b: []u8, off: u64, v: u64);
    fn ai_encode(b: []u8, id: u64, op: u64);
}
extern c "planner.h" { fn model_plan(user: []u8) -> u64; fn model_plan_available() -> bool; }
extern c "chat.h" { fn model_reset_context(); }

var request_id: u64 = 0;
var last_receipt: u64 = 0;
var response: [104]u8;

fn mind_write(s: str) {
    unsafe { platform_write(s as ptr<u8>, ustrlen(s)); }
}

fn mind_eq(b: []u8, s: str) -> bool {
    if b.len != ustrlen(s) { return false; }
    unsafe { for i in 0..b.len { if b[i] != s[i] { return false; } } }
    return true;
}

fn mind_exchange(req: []u8, expected: u64) -> bool {
    if usend(8, req) != 0 { mind_write("Error enviando la peticion.\n"); return false; }
    let n = urecv(9, response[..]);
    if n != 104 { mind_write("Respuesta IPC invalida.\n"); return false; }
    var id: u64 = 0;
    var op: u64 = 0;
    if req.len >= 16 { id = ai_get64(req, 8); }
    if req.len >= 24 { op = ai_get64(req, 16); }
    if ai_get64(response[..], 0) != 1 || ai_get64(response[..], 8) != id || ai_get64(response[..], 16) != op {
        mind_write("Respuesta IPC no corresponde a la peticion.\n"); return false;
    }
    let receipt = ai_get64(response[..], 32);
    if receipt <= last_receipt { mind_write("Recibo repetido.\n"); return false; }
    last_receipt = receipt;
    if ai_get64(response[..], 24) != expected { mind_write("La accion fue rechazada o fallo.\n"); return false; }
    return true;
}

fn mind_request(op: u64) -> bool {
    request_id += 1;
    var req: [40]u8;
    ai_encode(req[..], request_id, op);
    return mind_exchange(req[..], 0);
}

fn ai_query() -> bool { return mind_request(1); }
fn ai_execute(mode: u64) -> bool {
    if mode == 1 { return mind_request(2); }
    if mode == 0 { return mind_request(3); }
    return false;
}
fn ai_present() -> bool { return mind_request(4); }

fn mind_result(op: u64) {
    var b: [512]u8;
    var n = fmt_str(b[..], 0, "Recibo #");
    n = fmt_u64(b[..], n, last_receipt);
    if op == 1 {
        n = fmt_str(b[..], n, " | Sistema: ");
        n = fmt_u64(b[..], n, ai_get64(response[..], 48) / 256);
        n = fmt_str(b[..], n, " MiB libres / ");
        n = fmt_u64(b[..], n, ai_get64(response[..], 56) / 256);
        n = fmt_str(b[..], n, " MiB direccionables, ");
        n = fmt_u64(b[..], n, ai_get64(response[..], 64));
        n = fmt_str(b[..], n, " hilos vivos, ");
        n = fmt_u64(b[..], n, ai_get64(response[..], 40) / 1000);
        n = fmt_str(b[..], n, " s activo");
    }
    if op == 2 || op == 3 { n = fmt_str(b[..], n, " | Modo visual aplicado"); }
    if op == 4 { n = fmt_str(b[..], n, " | Panel de estado actualizado"); }
    n = fmt_str(b[..], n, " | modo=");
    if ai_get64(response[..], 88) == 1 { n = fmt_str(b[..], n, "foco"); }
    else { n = fmt_str(b[..], n, "normal"); }
    n = fmt_str(b[..], n, "\n");
    unsafe { platform_write(&b[0], n); }
}

fn mind_selftest() -> bool {
    if !mind_request(1) { return false; }
    let mode = ai_get64(response[..], 88);
    let revision = ai_get64(response[..], 96);
    var req: [40]u8;
    // Replay of the accepted graph request; cannot mutate or execute twice.
    ai_encode(req[..], request_id, 2);
    if !mind_exchange(req[..], 3) { return false; }
    request_id += 1;
    ai_encode(req[..], request_id, 2);
    ai_put64(req[..], 0, 99);
    if !mind_exchange(req[..], 2) { return false; }
    ai_encode(req[..], request_id, 99);
    if !mind_exchange(req[..], 4) { return false; }
    ai_encode(req[..], request_id, 2);
    ai_put64(req[..], 24, 1);
    if !mind_exchange(req[..], 5) { return false; }
    if !mind_exchange(req[0..16], 1) { return false; }
    // Reserved flags and oversized wire message must also fail closed.
    ai_encode(req[..], request_id, 2);
    ai_put64(req[..], 32, 1);
    if !mind_exchange(req[..], 5) { return false; }
    var large: [48]u8;
    ai_encode(large[..], request_id, 2);
    if !mind_exchange(large[..], 1) { return false; }
    if ai_get64(response[..], 88) != mode || ai_get64(response[..], 96) != revision { return false; }
    // The model has no System cap, no direct compositor right, no broker receive right.
    if usys(15, 7, 1, 0) != 0xFFFFFFFFFFFFFFFF { return false; }
    if usys(16, 3, 1, 0) != 0xFFFFFFFFFFFFFFFF { return false; }
    if usys(13, 3, 3, 0) != 0xFFFFFFFFFFFFFFFF { return false; }
    if usys(13, 3, 4, 0) != 0xFFFFFFFFFFFFFFFF { return false; }
    if usys(13, 3, 5, 0) != 0xFFFFFFFFFFFFFFFF { return false; }
    var p: u64 = 0;
    unsafe { p = &req[0] as u64; }
    if usys(6, 8, p, 40) != 0xFFFFFFFFFFFFFFFF { return false; }
    // Valid cap + unmapped/read-only/overflowing pointers must return ERR_ARG, not panic.
    if usys(1, 0, 0x30000000, 1) != 0xFFFFFFFFFFFFFFFE { return false; }
    if usys(6, 9, 0x40000000, 40) != 0xFFFFFFFFFFFFFFFE { return false; }
    if usys(5, 8, p, 0xFFFFFFFFFFFFFFFF) != 0xFFFFFFFFFFFFFFFE { return false; }
    if !mind_request(1) { return false; }
    return ai_get64(response[..], 88) == mode && ai_get64(response[..], 96) == revision;
}

fn mind_help() {
    mind_write("Erlan Mind | /estado  /modo foco  /modo normal  /panel\n");
    if model_plan_available() { mind_write("/ai <peticion> usa el modelo para elegir una accion. /ayuda\n"); }
    else { mind_write("Acciones del sistema: usa los comandos anteriores. /ayuda\n"); }
}

// Direct commands are explicit shortcuts. Only /ai invokes the learned planner.
// Ordinary chat output is never parsed as a command or used as authority.
fn mind_handle(line: []u8) -> bool {
    if line.len == 0 || line[0] != 47 { return false; }
    var op: u64 = 0;
    if mind_eq(line, "/estado") { op = 1; }
    else if mind_eq(line, "/modo foco") { op = 2; }
    else if mind_eq(line, "/modo normal") { op = 3; }
    else if mind_eq(line, "/panel") { op = 4; }
    else if mind_eq(line, "/ayuda") { mind_help(); }
    else if mind_eq(line, "/prueba") {
        if mind_selftest() { ulog("m4: selftest PASS"); mind_write("Pruebas de permisos y protocolo: PASS\n"); }
        else { ulog("m4: selftest FAIL"); mind_write("Pruebas: FAIL (ver serial)\n"); }
    }
    else if line.len > 4 && mind_eq(line[0..4], "/ai ") {
        if !model_plan_available() {
            mind_write("/ai aun no esta habilitado para este modelo. Usa /estado, /modo o /panel.\n");
            return true;
        }
        mind_write("Interpretando la peticion... Esc cancela.\n");
        platform_halo(2);
        op = model_plan(line[4..]);
        // Planner KV is independent in purpose but reuses runtime buffers in M4.
        model_reset_context();
        if platform_cancelled() {
            mind_write("Cancelado.\n"); ulog("m4: plan cancelled"); return true;
        }
        ulog_num("m4: plan=", op);
        if op > 4 { op = 0; }
        if op == 0 { mind_write("No se selecciono una accion compatible. Usa /ayuda.\n"); }
    }
    else { mind_write("Comando no reconocido. Usa /ayuda.\n"); }
    if op != 0 {
        var ok = false;
        if op == 1 { ok = ai_query(); }
        if op == 2 { ok = ai_execute(1); }
        if op == 3 { ok = ai_execute(0); }
        if op == 4 { ok = ai_present(); }
        if ok { mind_result(op); }
    }
    return true;
}
