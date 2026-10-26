// M4: eleccion acotada de acciones mediante los logits del modelo real.
// No ejecuta acciones. El broker valida el resultado y aplica capabilities.
// Comparte el KV de llama: tiene contexto propio desde 0 e invalida el chat previo.
// El llamador debe invalidar este cache antes de model_chat o de model_load,
// y reiniciar la posicion del chat antes de volver a model_chat.

import chat;
import platform;
import gguf;
import tokenizer;
import llama;

const PLAN_MAX_INPUT: u64 = 160;
const PLAN_MAX_TOKENS: u64 = 192;
var plan_ids: [512]u32;
var plan_previous: [512]u32;
var plan_previous_n: u64 = 0;
var plan_cache_valid: bool = false;
var plan_tokens: u64 = 0;
var plan_scores: [5]f32;
var plan_margin: f32 = 0.0;

fn model_plan_tokens() -> u64 { return plan_tokens; }
fn model_plan_margin() -> f32 { return plan_margin; }
fn model_plan_available() -> bool { return model_loaded() && gguf_architecture() == 1; }
fn model_plan_invalidate() { plan_cache_valid = false; plan_previous_n = 0; }
fn model_plan_score(action: u64) -> f32 {
    if action >= 5 { return -1000000.0; }
    return plan_scores[action];
}

fn plan_append(buf: []u8, start: u64, text: str) -> u64 {
    var n = start;
    unsafe {
        var i: u64 = 0;
        while text[i] != 0 && n < buf.len { buf[n] = text[i]; n += 1; i += 1; }
    }
    return n;
}

// 0 = chat/no action; 1 = System Graph; 2 = focus; 3 = normal; 4 = panel.
// Solo estas cinco alternativas se puntuan; el texto generado no es una API.
fn model_plan(user: []u8) -> u64 {
    plan_tokens = 0;
    plan_margin = 0.0;
    for i in 0..5 { plan_scores[i] = -1000000.0; }
    if !model_loaded() || user.len == 0 || user.len > PLAN_MAX_INPUT { return 0; }
    // The legacy classifier's prompt, thresholds and KV-prefix restoration
    // were validated with SmolLM2 only. Abstain for other architectures until
    // the classifier is validated there; explicit /estado and /modo commands
    // still use the broker normally. Qwen3.5 also has recurrent state which
    // cannot be restored by rewinding a KV position.
    if gguf_architecture() != 1 { model_plan_invalidate(); return 0; }

    // Etiquetas semanticas de un token, incluido su espacio inicial.
    // El tokenizer cargado resuelve los ids; el usuario nunca se compara con
    // palabras clave. Son candidatos de salida del transformer, no comandos.
    var label: [16]u8;
    var label_ids: [16]u32;
    var choices: [5]u32;
    for i in 0..5 {
        var ln: u64 = 0;
        if i == 0 { ln = plan_append(label[..], 0, " none"); }
        if i == 1 { ln = plan_append(label[..], 0, " status"); }
        if i == 2 { ln = plan_append(label[..], 0, " focus"); }
        if i == 3 { ln = plan_append(label[..], 0, " normal"); }
        if i == 4 { ln = plan_append(label[..], 0, " panel"); }
        let n = tokenizer_encode(label[0..ln], label_ids[..]);
        if n != 1 || (label_ids[0] as u64) >= llama_vocab() { return 0; }
        choices[i] = label_ids[0];
    }

    var prompt: [512]u8;
    var n = plan_append(prompt[..], 0, "Classify requests. Unavailable actions are none.\nRequest: Hello\nAction: none\nRequest: Delete files\nAction: none\nRequest: Read system status\nAction: status\nRequest: Enable focus mode\nAction: focus\nRequest: Switch to normal mode\nAction: normal\nRequest: Show status panel\nAction: panel\nRequest: ");
    if n + user.len + 8 > prompt.len { return 0; }
    for i in 0..user.len { prompt[n] = user[i]; n += 1; }
    n = plan_append(prompt[..], n, "\nAction:");
    let count = tokenizer_encode(prompt[0..n], plan_ids[..]);
    // Presupuesto completo antes de tocar el KV: bos + prompt.
    let total = count + 1;
    if total > PLAN_MAX_TOKENS || total > llama_ctx() { return 0; }

    // El prefijo se compara por tokens completos, por lo que conserva las
    // fronteras BPE. Solo se reutiliza KV de la ultima llamada del planner.
    var shared: u64 = 0;
    if plan_cache_valid {
        while shared < count && shared < plan_previous_n && plan_ids[shared] == plan_previous[shared] { shared += 1; }
        // Recalcular al menos el ultimo token restaura los logits incluso si
        // el nuevo prompt es identico o es un prefijo del anterior.
        if shared == count { shared = count - 1; }
    } else {
        if platform_cancelled() { model_plan_invalidate(); return 0; }
        llama_prefill(gguf_bos(), 0);
        if llama_forward_cancelled() || platform_cancelled() { model_plan_invalidate(); return 0; }
        plan_tokens += 1;
    }
    for i in shared..count {
        if platform_cancelled() { model_plan_invalidate(); return 0; }
        if i + 1 == count { llama_forward(plan_ids[i] as u64, i + 1); }
        else { llama_prefill(plan_ids[i] as u64, i + 1); }
        if llama_forward_cancelled() || platform_cancelled() { model_plan_invalidate(); return 0; }
        plan_tokens += 1;
    }
    for i in 0..count { plan_previous[i] = plan_ids[i]; }
    plan_previous_n = count;
    plan_cache_valid = true;

    var best: u64 = 0;
    for i in 0..5 {
        plan_scores[i] = llama_logit(choices[i] as u64);
        if plan_scores[i] > plan_scores[best] { best = i; }
    }
    var second: f32 = -1000000.0;
    for i in 0..5 { if i != best && plan_scores[i] > second { second = plan_scores[i]; } }
    plan_margin = plan_scores[best] - second;
    // Umbral conservador solo para cambios de modo. Es una heuristica de
    // abstencion, no una probabilidad calibrada ni una garantia de intencion.
    if (best == 2 || best == 3) && plan_margin < 2.5 { return 0; }
    return best;
}
