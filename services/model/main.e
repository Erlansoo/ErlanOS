// model: servicio de L2 que corre el LLM local dentro de ErlanOS.
// Mapea el GGUF (módulo de Limine) con la capability Module, pide memoria con Memory,
// dibuja con Console, lee teclas con Input. Sin esas capabilities no puede hacer nada.

extern c "platform.h" {
    fn platform_write(p: ptr<u8>, n: u64);
    fn platform_log(s: str);
    fn platform_getkey() -> u8;
    fn platform_clear_cancel();
    fn platform_halo(state: u64);
    fn platform_sleep_ms(ms: u64);
    fn platform_map_module(idx: u64) -> u64;
    fn platform_module_size(idx: u64) -> u64;
    fn platform_map_anon(pages: u64) -> u64;
}
extern c "ulib.h" { fn ulog(s: str) -> u64; fn ulog_num(label: str, v: u64); fn uexit(code: u64); }
extern c "chat.h" {
    fn model_load(data: u64, size: u64, arena_base: u64, arena_size: u64) -> bool;
    fn model_chat(user: []u8, max_new: u64) -> u64;
    fn model_arena_used() -> u64;
    fn model_warmup() -> bool;
    fn model_chat_context() -> u64;
}
extern c "controller.h" { fn mind_handle(line: []u8) -> bool; }
extern c "planner.h" { fn model_plan_invalidate(); }
extern c "document_client.h" { fn document_command(line: []u8) -> bool; fn document_editing() -> bool; fn document_key(c: u8); }
extern c "workspace_client.h" { fn workspace_client_init(); fn workspace_poll(); fn workspace_key(c: u8) -> bool; fn workspace_command(line: []u8) -> bool; fn workspace_chat_route(line: []u8) -> bool; fn workspace_chat(line: []u8); }

const ARENA_PAGES: u64 = 49152;   // 192 MiB, incluido estado recurrente Qwen3.5
const MAX_LINE: u64 = 240;

fn out_str(s: str) {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } platform_write(s as ptr<u8>, n); }
}

fn main() {
    ulog("model: iniciando");
    let msize = platform_module_size(0);
    if msize == 0 { out_str("no hay modelo (.gguf) en /boot\n"); ulog("model: sin modulo"); uexit(1); }
    let maddr = platform_map_module(0);
    if maddr == 0 { out_str("no se pudo mapear el modelo\n"); ulog("model: no se pudo mapear el modulo"); uexit(1); }
    let arena = platform_map_anon(ARENA_PAGES);
    if arena == 0 { out_str("sin memoria para el modelo\n"); ulog("model: sin memoria"); uexit(1); }
    ulog_num("model: gguf bytes=", msize);

    // El progreso normal va al serial: al arrancar queda el lienzo y sus puntos.
    // Los errores siguen siendo visibles en pantalla.
    model_plan_invalidate();
    if !model_load(maddr, msize, arena, ARENA_PAGES * 4096) {
        out_str("error cargando el modelo\n");
        uexit(2);
    }
    ulog_num("model: arena usada=", model_arena_used());
    ulog("model: preparando contexto");
    if model_warmup() { ulog_num("model: warmup tokens=", model_chat_context()); }
    ulog("model: listo");
    platform_halo(0);
    ulog("m4: ready");
    workspace_client_init();

    var line: [256]u8;
    var n: u64 = 0;
    var input_visible: bool = false;
    loop {
        workspace_poll();
        let c = platform_getkey();
        if c == 0 { platform_sleep_ms(20); continue; }
        if workspace_key(c) { continue; }
        if document_editing() {
            document_key(c);
            if !document_editing() { out_str("\x0e> "); }
            continue;
        }
        if c >= 128 { continue; }
        if !input_visible {
            if c == 27 || c == 8 { continue; }
            out_str("\x0e> ");
            input_visible = true;
        }
        if c == 27 {
            if n > 0 { platform_halo(0); }
            n = 0; out_str("\n\x0e> "); continue;
        }
        if c == 8 {
            if n > 0 {
                n -= 1;
                out_str("\x0f> ");
                unsafe { platform_write(&line[0], n); }
                if n == 0 { platform_halo(0); }
            }
            continue;
        }
        if c == 11 {
            if n < MAX_LINE {
                line[n] = 10; n += 1; out_str("\n");
                if n == 1 { platform_halo(1); }
            }
            continue;
        }
        if c == 10 {
            let submitted = n > 0;
            out_str("\n");
            if n > 0 {
                platform_clear_cancel();
                platform_halo(2);
                if !workspace_command(line[0..n]) && !document_command(line[0..n]) && !mind_handle(line[0..n]) && !workspace_chat_route(line[0..n]) {
                    model_plan_invalidate();
                    workspace_chat(line[0..n]);
                }
                platform_clear_cancel();
            }
            n = 0;
            // Tras un reinicio de contexto (cancelacion o contexto lleno) se vuelve a
            // preparar el mensaje de sistema en reposo; Esc lo interrumpe.
            if model_chat_context() == 0 { platform_clear_cancel(); model_warmup(); }
            if !document_editing() { out_str("\n\x0e> "); }
            if submitted { platform_halo(0); }
            ulog("m4: command done");
            continue;
        }
        if n < MAX_LINE {
            line[n] = c;
            n += 1;
            unsafe { platform_write(&line[n - 1], 1); }
            if n == 1 { platform_halo(1); }
        }
    }
}
