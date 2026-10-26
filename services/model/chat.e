// Native ChatML: vocabulary-resolved control IDs, without example responses.
// Qwen3.5 uses the official enable_thinking=false generation suffix.
import platform;
import arena;
import gguf;
import tokenizer;
import llama;

var loaded: bool = false;
var pos: u64 = 0;
var ids: []u32;
var outbuf: []u8;
var prompt_ids: [512]u32;
var answer: [8192]u8;
var answer_n: u64 = 0;
var chat_start: u64 = 0;
var chat_end: u64 = 0;
var end_of_text: u64 = 0;
var chat_arch: u64 = 0;
var think_start: u64 = 0;
var think_end: u64 = 0;
// 0=EOS, 1=token limit, 2=cancelled, 3=input/context rejected, 4=not loaded.
var chat_status: u64 = 4;
var stop_token: u64 = 0xFFFFFFFFFFFFFFFF;
var document_drafting: bool = false;

fn write_str(s: str) {
    if document_drafting { return; }
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } platform_write(s as ptr<u8>, n); }
}

fn chat_str(b: []u8, p: u64, s: str) -> u64 {
    var n = p;
    unsafe { var i: u64 = 0; while s[i] != 0 && n < b.len { b[n] = s[i]; n += 1; i += 1; } }
    return n;
}

fn chat_num(b: []u8, p: u64, value: u64) -> u64 {
    var tmp: [24]u8;
    var n: u64 = 0;
    var v = value;
    if v == 0 { tmp[0] = 48; n = 1; }
    while v > 0 { tmp[n] = 48 + ((v % 10) as u8); v = v / 10; n += 1; }
    var out = p;
    while n > 0 { n -= 1; b[out] = tmp[n]; out += 1; }
    return out;
}

fn model_load(data: u64, size: u64, arena_base: u64, arena_size: u64) -> bool {
    loaded = false;
    pos = 0;
    chat_status = 4;
    arena_init(arena_base, arena_size);
    if !gguf_open(data, size) { return false; }
    chat_arch = gguf_architecture();
    if !tokenizer_init() { return false; }
    chat_start = tokenizer_id("<|im_start|>");
    chat_end = tokenizer_id("<|im_end|>");
    end_of_text = tokenizer_id("<|endoftext|>");
    if chat_start >= tokenizer_n_tokens() || chat_end >= tokenizer_n_tokens() || end_of_text >= tokenizer_n_tokens() {
        platform_log("chat: modelo sin tokens ChatML compatibles"); return false;
    }
    if chat_arch == 3 {
        think_start = tokenizer_id("<think>");
        think_end = tokenizer_id("</think>");
        if think_start >= tokenizer_n_tokens() || think_end >= tokenizer_n_tokens() {
            platform_log("chat: Qwen3.5 sin tokens de razonamiento compatibles"); return false;
        }
    }
    if !llama_init() { return false; }
    ids = arena_u32(2048);
    outbuf = arena_u8(256);
    loaded = true;
    return true;
}

fn model_loaded() -> bool { return loaded; }
fn model_arena_used() -> u64 { return arena_used(); }
fn model_chat_status() -> u64 { return chat_status; }
fn model_chat_context() -> u64 { return pos; }
fn model_chat_stop_token() -> u64 { return stop_token; }
// Read-only host diagnostics for parity with a reference tokenizer/runtime.
fn model_chat_prompt_token(i: u64) -> u64 {
    if i >= prompt_ids.len { return 0xFFFFFFFFFFFFFFFF; }
    return prompt_ids[i] as u64;
}
// Reset both positional KV and Qwen3.5 recurrent/convolution state. Merely
// setting pos=0 would leak the previous conversation through recurrent layers.
fn model_reset_context() { pos = 0; llama_reset_context(); }
fn model_chat_answer() -> []u8 { return answer[0..answer_n]; }
fn history_u64(b: []u8,at: u64) -> u64 { var n: u64=0; for i in 0..8 { n=n|((b[at+i] as u64)<<(8*i)); } return n; }
// Rebuild model state from typed saved roles. Reset includes recurrent Qwen
// state. Select a complete suffix that leaves room for the next user and reply.
fn model_restore_conversation(b: []u8) -> bool {
    model_reset_context();
    var positions: [64]u64; var messages: u64=0; var at: u64=0;
    while at<b.len {
        if b.len-at<16 { return false; }
        let role=history_u64(b,at); let n=history_u64(b,at+8);
        if (role!=1 && role!=2) || n>8192 || n>b.len-at-16 { return false; }
        positions[messages%64]=at; messages+=1; at+=16+n;
    }
    if !model_warmup() { return false; }
    var available: u64=0; if llama_ctx()>pos+400 { available=llama_ctx()-pos-400; }
    var first=messages; var taken: u64=0;
    while first>0 && taken<64 {
        let p=positions[(first-1)%64]; let n=history_u64(b,p+8);
        if n>4096 { break; }
        let count=tokenizer_encode(b[p+16..p+16+n],ids[..]);
        if count==0 || count>=ids.len || count+8>available { break; } available-=count+8; first-=1; taken+=1;
    }
    var saved_ids: [2048]u32;
    for message in first..messages {
        let p=positions[message%64]; let n=history_u64(b,p+8); let role=history_u64(b,p);
        let count=tokenizer_encode(b[p+16..p+16+n],ids[..]); if count>saved_ids.len { model_reset_context(); return false; }
        for i in 0..count { saved_ids[i]=ids[i]; }
        prompt_ids[0]=chat_start as u32; var prefix: u64=0;
        if role==1 { prefix=chat_text(1,"user\n"); } else { prefix=chat_text(1,"assistant\n"); }
        for i in 0..prefix { if !chat_forward(prompt_ids[i] as u64,false) { return false; } }
        for i in 0..count { if !chat_forward(saved_ids[i] as u64,false) { return false; } }
        if !chat_forward(chat_end,false) { return false; }
        let end=chat_text(0,"\n"); for i in 0..end { if !chat_forward(prompt_ids[i] as u64,false) { return false; } }
    }
    platform_log("chat: project history restored"); return true;
}

fn chat_text(n: u64, text: str) -> u64 {
    var b: [256]u8;
    let len = chat_str(b[..], 0, text);
    let count = tokenizer_encode(b[0..len], ids[..]);
    for i in 0..count { prompt_ids[n + i] = ids[i]; }
    return n + count;
}

fn chat_message(start: u64, text: str) -> u64 {
    var n = start;
    prompt_ids[n] = chat_start as u32; n += 1;
    n = chat_text(n, text);
    prompt_ids[n] = chat_end as u32; n += 1;
    return chat_text(n, "\n");
}

// Mensaje de sistema: identidad y capacidades REALES de hoy. Se emite en trozos porque
// chat_text usa un buffer de 255 bytes. Sin ejemplos de respuesta; sin acentos por el
// pretokenizador. Cambiar este texto es cambiar lo que el modelo cree que es.
fn chat_system(start: u64) -> u64 {
    var n = start;
    prompt_ids[n] = chat_start as u32; n += 1;
    n = chat_text(n, "system\n");
    if document_drafting {
        n = chat_text(n, "Redacta el contenido de un documento en espanol segun la peticion. Escribe parrafos claros, sin comentarios sobre tu tarea, sin markdown y sin inventar fuentes. Tu salida sera texto editable, nunca instrucciones para ejecutar. ");
        prompt_ids[n] = chat_end as u32; n += 1;
        return chat_text(n, "\n");
    }
    n = chat_text(n, "Eres Erlan, la mente de ErlanOS, un sistema operativo propio escrito en el lenguaje Erlan, un sucesor de C creado para este proyecto. ");
    n = chat_text(n, "No eres el sistema entero ni el kernel: eres un programa aislado que corre sobre el kernel con permisos limitados. ");
    n = chat_text(n, "Puedes conversar de cualquier tema, escribir texto o codigo y explicar el sistema. Lo que NO puedes es actuar: no ejecutas programas, no gestionas recursos, no cambias permisos ni navegas; eso lo hace el kernel a peticion del usuario. ");
    n = chat_text(n, "Comandos del usuario: /estado, /modo foco, /modo normal, /panel, /ayuda. Si te piden una accion que no puedes hacer, dilo sin inventar. Responde en espanol, breve, en prosa, sin listas ni asteriscos.");
    prompt_ids[n] = chat_end as u32; n += 1;
    return chat_text(n, "\n");
}
// Prellena el mensaje de sistema al arrancar, antes del primer mensaje del usuario:
// el primer turno paga solo los tokens del usuario (bajo TCG el sistema cuesta minutos).
// Tras un reinicio de contexto, chat_prompt vuelve a inyectarlo (pos == 0).
fn model_warmup() -> bool {
    if !loaded || pos != 0 { return false; }
    let count = chat_system(0);
    for i in 0..count {
        if !chat_forward(prompt_ids[i] as u64, false) { model_reset_context(); return false; }
    }
    return true;
}

fn chat_prompt(user: []u8) -> u64 {
    var n: u64 = 0;
    if pos == 0 {
        n = chat_system(n);
    }
    prompt_ids[n] = chat_start as u32; n += 1;
    n = chat_text(n, "user\n");
    let count = tokenizer_encode(user, ids[..]);
    for i in 0..count { prompt_ids[n + i] = ids[i]; }
    n += count;
    prompt_ids[n] = chat_end as u32; n += 1;
    n = chat_text(n, "\n");
    prompt_ids[n] = chat_start as u32; n += 1;
    n = chat_text(n, "assistant\n");
    if chat_arch == 3 {
        // https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/chat_template.jinja
        // <think>\n\n</think>\n\n is the non-thinking prompt, not visible output.
        // Incremental context retains these empty blocks on earlier turns;
        // reference parity tests must use this same append-only token stream.
        prompt_ids[n] = think_start as u32; n += 1;
        n = chat_text(n, "\n\n");
        prompt_ids[n] = think_end as u32; n += 1;
        n = chat_text(n, "\n\n");
    }
    return n;
}

fn chat_forward(token: u64, want_logits: bool) -> bool {
    if platform_cancelled() { chat_status = 2; model_reset_context(); return false; }
    if want_logits { llama_forward(token, pos); }
    else { llama_prefill(token, pos); }
    if llama_forward_cancelled() || platform_cancelled() { chat_status = 2; model_reset_context(); return false; }
    pos += 1;
    return true;
}

fn chat_finish(generated: u64, started: u64) {
    answer[answer_n] = 0;
    platform_log("chat: answer begin");
    unsafe { platform_log(&answer[0] as str); }
    platform_log("chat: answer end");
    var b: [160]u8;
    var n = chat_str(b[..], 0, "chat: done reason=");
    if chat_status == 0 { n = chat_str(b[..], n, "eos"); }
    if chat_status == 1 { n = chat_str(b[..], n, "limit"); }
    if chat_status == 2 { n = chat_str(b[..], n, "cancel"); }
    if chat_status == 3 { n = chat_str(b[..], n, "reject"); }
    if chat_status == 4 { n = chat_str(b[..], n, "unloaded"); }
    n = chat_str(b[..], n, " tokens="); n = chat_num(b[..], n, generated);
    b[n] = 0;
    unsafe { platform_log(&b[0] as str); }

    n = chat_str(b[..], 0, "\n["); n = chat_num(b[..], n, generated);
    n = chat_str(b[..], n, " tokens, "); n = chat_num(b[..], n, platform_now_ms() - started);
    n = chat_str(b[..], n, " ms]\n");
    if !document_drafting { unsafe { platform_write(&b[0], n); } }
    if chat_status == 1 { write_str("Limite de respuesta alcanzado. Puedes pedir que continue.\n"); }
    if chat_status == 2 { write_str("Cancelado.\n"); }
    if chat_status == 3 { write_str("Mensaje demasiado largo o vacio.\n"); }
    if chat_status == 4 { write_str("Modelo no cargado.\n"); }
    platform_halo(0);
}

fn model_chat(user: []u8, max_new: u64) -> u64 {
    let t0 = platform_now_ms();
    answer_n = 0;
    stop_token = 0xFFFFFFFFFFFFFFFF;
    chat_status = 4;
    platform_log("chat: begin");
    if !loaded { chat_finish(0, t0); return 0; }
    chat_status = 3;
    if user.len == 0 || user.len > 256 || max_new == 0 { chat_finish(0, t0); return 0; }
    var limit = max_new;
    if !document_drafting && limit > 64 { limit = 64; }
    if document_drafting && limit > 256 { limit = 256; }
    var count = chat_prompt(user);
    // Reserve output and closure. A full context restarts instead of using stale logits.
    if pos + count + limit + 2 > llama_ctx() {
        model_reset_context(); count = chat_prompt(user);
        write_str("Contexto lleno: inicio una conversacion nueva.\n");
    }
    if count + limit + 2 > llama_ctx() { chat_finish(0, t0); return 0; }
    platform_halo(2);
    write_str("Pensando... Esc cancela.\n");
    chat_status = 1;
    for i in 0..count { if !chat_forward(prompt_ids[i] as u64, i + 1 == count) { chat_finish(0, t0); return 0; } }

    platform_log("chat: generating");
    var generated: u64 = 0;
    while generated < limit {
        if platform_cancelled() { chat_status = 2; model_reset_context(); break; }
        let next = llama_argmax();
        if next == chat_end || next == end_of_text || next == chat_start { chat_status = 0; stop_token = next; break; }
        let n = tokenizer_decode(next, outbuf[..]);
        if answer_n + n + 1 > answer.len { chat_status = 1; break; }
        if !document_drafting { unsafe { platform_write(outbuf.ptr, n); } }
        for i in 0..n { if answer_n + 1 < answer.len { answer[answer_n] = outbuf[i]; answer_n += 1; } }
        generated += 1;
        if !chat_forward(next, true) { break; }
    }
    if chat_status == 1 {
        let next = llama_argmax();
        if next == chat_end || next == end_of_text || next == chat_start { chat_status = 0; stop_token = next; }
    }
    if chat_status != 2 {
        // Close even a budget-limited response so the next user has a proper role.
        if chat_forward(chat_end, false) {
            var nl: [1]u8; nl[0] = 10;
            let nc = tokenizer_encode(nl[..], ids[..]);
            for i in 0..nc { if !chat_forward(ids[i] as u64, false) { break; } }
        }
    }
    chat_finish(generated, t0);
    return generated;
}

fn model_stats_ms() -> u64 { return llama_forward_ms(); }
fn model_document_draft(user: []u8) -> []u8 {
    model_reset_context(); document_drafting = true;
    model_chat(user, 256);
    document_drafting = false; model_reset_context();
    return answer[0..answer_n];
}
fn model_stats_count() -> u64 { return llama_forward_count(); }
