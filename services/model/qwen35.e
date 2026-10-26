// Qwen3.5 de texto: atención GQA con compuerta y GatedDeltaNet recurrente.
// GGUF ya contiene norm+1, -exp(A_log) y el orden Q/K/V del convertidor.
// Referencia: llama.cpp src/models/qwen35.cpp y Transformers qwen3_next.
// No se ejecutan el encoder visual ni los bloques auxiliares MTP.

import platform;
import arena;
import umath;
import mathx;
import llama;
import gguf;

var q35_layers: u64 = 0; var q35_dim: u64 = 0; var q35_hidden: u64 = 0;
var q35_heads: u64 = 0; var q35_kvheads: u64 = 0; var q35_head: u64 = 0;
var q35_qdim: u64 = 0; var q35_kvdim: u64 = 0;
var q35_vocab: u64 = 0; var q35_ctx: u64 = 0; var q35_eps: f32 = 0.000001;
var q35_lheads: u64 = 0; var q35_lhead: u64 = 0; var q35_ldim: u64 = 0;
var q35_kernel: u64 = 0; var q35_rope_dim: u64 = 0;
var q35_linear_count: u64 = 0; var q35_full_count: u64 = 0;
var q35_weights: []u64; var q35_types: []u32;
var q35_kind: []u64; var q35_slot: []u64;
var q35_embd: u64 = 0; var q35_embdtype: u32 = 0;
var q35_norm: u64 = 0; var q35_output: u64 = 0; var q35_outputtype: u32 = 0;
var q35_x: []f32; var q35_xb: []f32; var q35_tmp: []f32;
var q35_h: []f32; var q35_h2: []f32;
var q35_q: []f32; var q35_k: []f32; var q35_v: []f32; var q35_gate: []f32;
var q35_packed: []f32; var q35_conv: []f32; var q35_beta: []f32; var q35_alpha: []f32;
var q35_att: []f32; var q35_attout: []f32; var q35_logits: []f32;
var q35_states: []f32; var q35_history: []f32;
var q35_kcache: []f32; var q35_vcache: []f32;
var q35_cos: []f32; var q35_sin: []f32;
var q35_pos: u64 = 0; var q35_valid: bool = false;
var q35_ms: u64 = 0; var q35_count: u64 = 0; var q35_cancelled: bool = false;

fn q35_find(layer: u64, name: str) -> u64 {
    var buf: [96]u8;
    let n = name_layer(buf[..], layer, name);
    return gguf_find(buf[0..n]);
}
fn q35_plain(name: str) -> u64 {
    var buf: [96]u8; let n = name_plain(buf[..], name); return gguf_find(buf[0..n]);
}
fn q35_load(layer: u64, slot: u64, name: str, cols: u64, rows: u64, vector: bool, f32_only: bool) -> bool {
    let t = q35_find(layer, name);
    if t == gguf_n_tensors() { platform_log("qwen35: falta tensor"); platform_log(name); return false; }
    if !tensor_valid(t, cols, rows, vector, f32_only) { platform_log(name); return false; }
    q35_weights[layer * 16 + slot] = gguf_tensor_addr(t);
    q35_types[layer * 16 + slot] = gguf_tensor_type(t);
    return true;
}
fn q35_w(l: u64, s: u64) -> u64 { return q35_weights[l * 16 + s]; }
fn q35_t(l: u64, s: u64) -> u32 { return q35_types[l * 16 + s]; }

fn qwen35_init() -> bool {
    q35_layers = gguf_n_layers(); q35_dim = gguf_dim(); q35_hidden = gguf_hidden();
    q35_heads = gguf_n_heads(); q35_kvheads = gguf_n_kv_heads(); q35_vocab = gguf_vocab_n();
    q35_eps = gguf_eps(); q35_ctx = gguf_ctx_len(); if q35_ctx > 512 { q35_ctx = 512; }
    q35_head = gguf_meta_u64("qwen35.attention.key_length", 0);
    let vh = gguf_meta_u64("qwen35.attention.value_length", q35_head);
    q35_kernel = gguf_meta_u64("qwen35.ssm.conv_kernel", 0);
    q35_lhead = gguf_meta_u64("qwen35.ssm.state_size", 0);
    q35_lheads = gguf_meta_u64("qwen35.ssm.time_step_rank", 0);
    let kh = gguf_meta_u64("qwen35.ssm.group_count", 0);
    q35_ldim = gguf_meta_u64("qwen35.ssm.inner_size", 0);
    q35_rope_dim = gguf_rope_dim();
    let mtp = gguf_meta_u64("qwen35.nextn_predict_layers", 0);
    if mtp >= q35_layers { platform_log("qwen35: MTP invalido"); return false; }
    q35_layers -= mtp;
    // Bounded text profiles: equal key/value linear heads (0.8B/2B). A wider
    // grouped linear profile must be implemented and validated explicitly.
    if q35_layers == 0 || q35_layers > 99 || q35_dim == 0 || q35_dim > 8192 ||
       q35_hidden == 0 || q35_hidden > 8192 || q35_heads == 0 || q35_heads > 64 ||
       q35_kvheads == 0 || q35_kvheads > q35_heads || q35_head == 0 || q35_head > 512 ||
       vh != q35_head || q35_heads % q35_kvheads != 0 ||
       q35_lheads == 0 || q35_lheads > 64 || kh != q35_lheads ||
       q35_lhead == 0 || q35_lhead > 256 || q35_ldim != q35_lheads * q35_lhead || q35_ldim > 8192 ||
       q35_kernel < 2 || q35_kernel > 8 || q35_vocab == 0 || q35_vocab > 262144 || q35_ctx == 0 ||
       q35_rope_dim == 0 || q35_rope_dim > q35_head || q35_rope_dim % 2 != 0 ||
       !(q35_eps > 0.0) || !(gguf_rope_base() > 0.0) {
        platform_log("qwen35: perfil/dimensiones no soportados"); return false;
    }
    q35_qdim = q35_heads * q35_head; q35_kvdim = q35_kvheads * q35_head;
    if q35_qdim > 8192 { platform_log("qwen35: proyeccion Q demasiado ancha"); return false; }
    let weight_bytes = q35_layers * 208 + 512;
    if arena_used() > arena_size() || weight_bytes > arena_size() - arena_used() { return false; }
    q35_weights = arena_u64(q35_layers * 16); q35_types = arena_u32(q35_layers * 16);
    q35_kind = arena_u64(q35_layers); q35_slot = arena_u64(q35_layers);
    q35_linear_count = 0; q35_full_count = 0;
    for l in 0..q35_layers {
        if !q35_load(l, 0, "attn_norm.weight", q35_dim, 1, true, true) { return false; }
        if !q35_load(l, 1, "post_attention_norm.weight", q35_dim, 1, true, true) { return false; }
        if !q35_load(l, 2, "ffn_gate.weight", q35_dim, q35_hidden, false, false) { return false; }
        if !q35_load(l, 3, "ffn_up.weight", q35_dim, q35_hidden, false, false) { return false; }
        if !q35_load(l, 4, "ffn_down.weight", q35_hidden, q35_dim, false, false) { return false; }
        if q35_find(l, "attn_qkv.weight") != gguf_n_tensors() {
            q35_kind[l] = 1; q35_slot[l] = q35_linear_count; q35_linear_count += 1;
            if !q35_load(l, 5, "attn_qkv.weight", q35_dim, q35_ldim * 3, false, false) { return false; }
            if !q35_load(l, 6, "attn_gate.weight", q35_dim, q35_ldim, false, false) { return false; }
            if !q35_load(l, 7, "ssm_beta.weight", q35_dim, q35_lheads, false, false) { return false; }
            if !q35_load(l, 8, "ssm_alpha.weight", q35_dim, q35_lheads, false, false) { return false; }
            if !q35_load(l, 9, "ssm_conv1d.weight", q35_kernel, q35_ldim * 3, false, true) { return false; }
            if !q35_load(l, 10, "ssm_dt.bias", q35_lheads, 1, true, true) { return false; }
            if !q35_load(l, 11, "ssm_a", q35_lheads, 1, true, true) { return false; }
            if !q35_load(l, 12, "ssm_norm.weight", q35_lhead, 1, true, true) { return false; }
            if !q35_load(l, 13, "ssm_out.weight", q35_ldim, q35_dim, false, false) { return false; }
        } else {
            q35_kind[l] = 0; q35_slot[l] = q35_full_count; q35_full_count += 1;
            if !q35_load(l, 5, "attn_q.weight", q35_dim, q35_qdim * 2, false, false) { return false; }
            if !q35_load(l, 6, "attn_k.weight", q35_dim, q35_kvdim, false, false) { return false; }
            if !q35_load(l, 7, "attn_v.weight", q35_dim, q35_kvdim, false, false) { return false; }
            if !q35_load(l, 8, "attn_output.weight", q35_qdim, q35_dim, false, false) { return false; }
            if !q35_load(l, 9, "attn_q_norm.weight", q35_head, 1, true, true) { return false; }
            if !q35_load(l, 10, "attn_k_norm.weight", q35_head, 1, true, true) { return false; }
        }
    }
    var t = q35_plain("token_embd.weight");
    if t == gguf_n_tensors() || !tensor_valid(t, q35_dim, q35_vocab, false, false) { return false; }
    q35_embd = gguf_tensor_addr(t); q35_embdtype = gguf_tensor_type(t);
    t = q35_plain("output_norm.weight");
    if t == gguf_n_tensors() || !tensor_valid(t, q35_dim, 1, true, true) { return false; }
    q35_norm = gguf_tensor_addr(t);
    t = q35_plain("output.weight");
    if t == gguf_n_tensors() { q35_output = q35_embd; q35_outputtype = q35_embdtype; }
    else {
        if !tensor_valid(t, q35_dim, q35_vocab, false, false) { return false; }
        q35_output = gguf_tensor_addr(t); q35_outputtype = gguf_tensor_type(t);
    }
    var wide = q35_qdim; if q35_ldim > wide { wide = q35_ldim; }
    var packed_size = q35_ldim * 3; if q35_qdim * 2 > packed_size { packed_size = q35_qdim * 2; }
    let state_n = q35_linear_count * q35_lheads * q35_lhead * q35_lhead;
    let hist_n = q35_linear_count * q35_ldim * 3 * (q35_kernel - 1);
    let kv_n = q35_full_count * q35_ctx * q35_kvdim;
    let needed = 4 * (3 * q35_dim + 2 * q35_hidden + 5 * wide + packed_size + q35_ldim * 3 +
                 2 * q35_lheads + q35_heads * q35_ctx + q35_vocab + state_n + hist_n + 2 * kv_n +
                 q35_ctx * q35_rope_dim) + 4096;
    if arena_used() > arena_size() || needed > arena_size() - arena_used() {
        platform_log("qwen35: arena insuficiente para estado/KV"); return false;
    }
    q35_x = arena_f32(q35_dim); q35_xb = arena_f32(q35_dim); q35_tmp = arena_f32(q35_dim);
    q35_h = arena_f32(q35_hidden); q35_h2 = arena_f32(q35_hidden);
    q35_q = arena_f32(wide); q35_k = arena_f32(wide); q35_v = arena_f32(wide);
    q35_gate = arena_f32(wide); q35_attout = arena_f32(wide);
    q35_packed = arena_f32(packed_size); q35_conv = arena_f32(q35_ldim * 3);
    q35_beta = arena_f32(q35_lheads); q35_alpha = arena_f32(q35_lheads);
    q35_att = arena_f32(q35_heads * q35_ctx); q35_logits = arena_f32(q35_vocab);
    q35_states = arena_f32(state_n); q35_history = arena_f32(hist_n);
    q35_kcache = arena_f32(kv_n); q35_vcache = arena_f32(kv_n);
    let half = q35_rope_dim / 2;
    q35_cos = arena_f32(q35_ctx * half); q35_sin = arena_f32(q35_ctx * half);
    for i in 0..half {
        let freq = mx_pow(gguf_rope_base(), -((2 * i) as f32) / (q35_rope_dim as f32));
        for p in 0..q35_ctx {
            q35_cos[p * half + i] = mx_cos((p as f32) * freq);
            q35_sin[p * half + i] = mx_sin((p as f32) * freq);
        }
    }
    q35_ms = 0; q35_count = 0; qwen35_reset_context();
    platform_log("qwen35: GatedDeltaNet + GQA cargados"); return true;
}

fn qwen35_reset_context() {
    for i in 0..q35_states.len { q35_states[i] = 0.0; }
    for i in 0..q35_history.len { q35_history[i] = 0.0; }
    // Old KV beyond pos is never read; reset the logical cursor as well as all
    // recurrent state. Reset is required after EVERY cancelled partial step.
    q35_pos = 0; q35_valid = true; q35_cancelled = false;
}
fn q35_rms(out: []f32, input: []f32, w: u64, n: u64) {
    var sum: f32 = 0.0;
    for i in 0..n { sum += input[i] * input[i]; }
    let inv = 1.0 / mx_sqrt(sum / (n as f32) + q35_eps);
    for i in 0..n { out[i] = (input[i] * inv) * ld_f32(w + i * 4); }
}
fn q35_rope(vec: []f32, heads: u64, pos: u64) {
    // For text, all MRoPE axes have the same position. IMRoPE therefore equals
    // split-half rotation of the first rope_dim channels, leaving the tail.
    let half = q35_rope_dim / 2;
    for h in 0..heads {
        for i in 0..half {
            let a = h * q35_head + i; let b = a + half;
            let c = q35_cos[pos * half + i]; let s = q35_sin[pos * half + i];
            let x0 = vec[a]; let x1 = vec[b];
            vec[a] = x0 * c - x1 * s; vec[b] = x0 * s + x1 * c;
        }
    }
}
fn q35_sigmoid(x: f32) -> f32 { return 1.0 / (1.0 + mx_exp(-x)); }
fn q35_softplus(x: f32) -> f32 {
    if x > 20.0 { return x; }
    if x < -20.0 { return mx_exp(x); }
    return mx_ln(1.0 + mx_exp(x));
}
fn q35_full(l: u64, pos: u64) {
    matmul(q35_packed, q35_xb, q35_w(l, 5), q35_t(l, 5), q35_qdim * 2, q35_dim);
    for h in 0..q35_heads {
        for i in 0..q35_head {
            q35_q[h * q35_head + i] = q35_packed[h * q35_head * 2 + i];
            q35_gate[h * q35_head + i] = q35_packed[h * q35_head * 2 + q35_head + i];
        }
        let off = h * q35_head;
        q35_rms(q35_q[off..off + q35_head], q35_q[off..off + q35_head], q35_w(l, 9), q35_head);
    }
    matmul(q35_k, q35_xb, q35_w(l, 6), q35_t(l, 6), q35_kvdim, q35_dim);
    matmul(q35_v, q35_xb, q35_w(l, 7), q35_t(l, 7), q35_kvdim, q35_dim);
    for h in 0..q35_kvheads {
        let off = h * q35_head;
        q35_rms(q35_k[off..off + q35_head], q35_k[off..off + q35_head], q35_w(l, 10), q35_head);
    }
    q35_rope(q35_q, q35_heads, pos); q35_rope(q35_k, q35_kvheads, pos);
    let base = q35_slot[l] * q35_ctx * q35_kvdim;
    for i in 0..q35_kvdim {
        q35_kcache[base + pos * q35_kvdim + i] = q35_k[i];
        q35_vcache[base + pos * q35_kvdim + i] = q35_v[i];
    }
    let scale = 1.0 / mx_sqrt(q35_head as f32);
    let group = q35_heads / q35_kvheads;
    for h in 0..q35_heads {
        let off = h * q35_head; let kvh = h / group; let ao = h * q35_ctx;
        var highest: f32 = -1.0e30;
        for t in 0..pos + 1 {
            var sum: f32 = 0.0; let ko = base + t * q35_kvdim + kvh * q35_head;
            for i in 0..q35_head { sum += q35_q[off + i] * q35_kcache[ko + i]; }
            sum *= scale; q35_att[ao + t] = sum; if sum > highest { highest = sum; }
        }
        var total: f32 = 0.0;
        for t in 0..pos + 1 { let a = mx_exp(q35_att[ao + t] - highest); q35_att[ao + t] = a; total += a; }
        for i in 0..q35_head { q35_attout[off + i] = 0.0; }
        for t in 0..pos + 1 {
            let a = q35_att[ao + t] / total;
            let vo = base + t * q35_kvdim + kvh * q35_head;
            for i in 0..q35_head { q35_attout[off + i] += a * q35_vcache[vo + i]; }
        }
        for i in 0..q35_head { q35_attout[off + i] *= q35_sigmoid(q35_gate[off + i]); }
    }
    matmul(q35_tmp, q35_attout, q35_w(l, 8), q35_t(l, 8), q35_dim, q35_qdim);
}

// One head of the recurrent delta rule. State layout [key][value]. Inputs Q/K
// are already L2-normalized; Q also includes 1/sqrt(head_dim). Exposed for a
// double-precision, independent host recurrence test.
fn q35_delta(state: []f32, query: []f32, key: []f32, value: []f32, out: []f32, n: u64, decay: f32, beta: f32) {
    for j in 0..n {
        var memory: f32 = 0.0;
        for i in 0..n {
            let off = i * n + j;
            state[off] *= decay;
            memory += state[off] * key[i];
        }
        let delta = (value[j] - memory) * beta;
        var result: f32 = 0.0;
        for i in 0..n {
            let off = i * n + j;
            state[off] += key[i] * delta;
            result += state[off] * query[i];
        }
        out[j] = result;
    }
}
fn q35_linear(l: u64) {
    matmul(q35_packed, q35_xb, q35_w(l, 5), q35_t(l, 5), q35_ldim * 3, q35_dim);
    matmul(q35_gate, q35_xb, q35_w(l, 6), q35_t(l, 6), q35_ldim, q35_dim);
    matmul(q35_beta, q35_xb, q35_w(l, 7), q35_t(l, 7), q35_lheads, q35_dim);
    matmul(q35_alpha, q35_xb, q35_w(l, 8), q35_t(l, 8), q35_lheads, q35_dim);
    let hist_len = q35_kernel - 1;
    let hist_base = q35_slot[l] * q35_ldim * 3 * hist_len;
    for c in 0..q35_ldim * 3 {
        let ho = hist_base + c * hist_len; let wo = q35_w(l, 9) + c * q35_kernel * 4;
        var sum: f32 = 0.0;
        for i in 0..hist_len { sum += q35_history[ho + i] * ld_f32(wo + i * 4); }
        sum += q35_packed[c] * ld_f32(wo + hist_len * 4);
        for i in 0..hist_len - 1 { q35_history[ho + i] = q35_history[ho + i + 1]; }
        q35_history[ho + hist_len - 1] = q35_packed[c];
        q35_conv[c] = mx_silu(sum);
    }
    for h in 0..q35_lheads {
        let off = h * q35_lhead;
        var qss: f32 = 0.0; var kss: f32 = 0.0;
        for i in 0..q35_lhead {
            let qv = q35_conv[off + i]; let kv = q35_conv[q35_ldim + off + i];
            qss += qv * qv; kss += kv * kv;
        }
        let qs = 1.0 / mx_sqrt(qss + q35_eps) / mx_sqrt(q35_lhead as f32);
        let ks = 1.0 / mx_sqrt(kss + q35_eps);
        for i in 0..q35_lhead {
            q35_q[off + i] = q35_conv[off + i] * qs;
            q35_k[off + i] = q35_conv[q35_ldim + off + i] * ks;
            q35_v[off + i] = q35_conv[q35_ldim * 2 + off + i];
        }
        let beta = q35_sigmoid(q35_beta[h]);
        let alpha = q35_softplus(q35_alpha[h] + ld_f32(q35_w(l, 10) + h * 4));
        let decay = mx_exp(alpha * ld_f32(q35_w(l, 11) + h * 4));
        let sb = (q35_slot[l] * q35_lheads + h) * q35_lhead * q35_lhead;
        let end = off + q35_lhead;
        q35_delta(q35_states[sb..sb + q35_lhead * q35_lhead], q35_q[off..end], q35_k[off..end],
                  q35_v[off..end], q35_attout[off..end], q35_lhead, decay, beta);
        q35_rms(q35_attout[off..end], q35_attout[off..end], q35_w(l, 12), q35_lhead);
        for i in off..end { q35_attout[i] *= mx_silu(q35_gate[i]); }
    }
    matmul(q35_tmp, q35_attout, q35_w(l, 13), q35_t(l, 13), q35_dim, q35_ldim);
}
fn q35_abort() { q35_cancelled = true; q35_valid = false; }
fn qwen35_step(token: u64, pos: u64, need_logits: bool) {
    q35_cancelled = false;
    if !q35_valid || pos != q35_pos || pos >= q35_ctx || token >= q35_vocab {
        platform_log("qwen35: contexto requiere reinicio"); q35_abort(); return;
    }
    if platform_cancelled() { q35_abort(); return; }
    let start = platform_now_ms();
    dequant_row(q35_x, q35_embd, q35_embdtype, token, q35_dim);
    for l in 0..q35_layers {
        if platform_cancelled() { q35_abort(); return; }
        q35_rms(q35_xb, q35_x, q35_w(l, 0), q35_dim);
        if q35_kind[l] == 1 { q35_linear(l); } else { q35_full(l, pos); }
        for i in 0..q35_dim { q35_x[i] += q35_tmp[i]; }
        q35_rms(q35_xb, q35_x, q35_w(l, 1), q35_dim);
        matmul(q35_h, q35_xb, q35_w(l, 2), q35_t(l, 2), q35_hidden, q35_dim);
        matmul(q35_h2, q35_xb, q35_w(l, 3), q35_t(l, 3), q35_hidden, q35_dim);
        for i in 0..q35_hidden { q35_h[i] = mx_silu(q35_h[i]) * q35_h2[i]; }
        matmul(q35_xb, q35_h, q35_w(l, 4), q35_t(l, 4), q35_dim, q35_hidden);
        for i in 0..q35_dim { q35_x[i] += q35_xb[i]; }
        platform_yield();
    }
    if platform_cancelled() { q35_abort(); return; }
    if need_logits {
        q35_rms(q35_x, q35_x, q35_norm, q35_dim);
        matmul(q35_logits, q35_x, q35_output, q35_outputtype, q35_vocab, q35_dim);
    }
    if platform_cancelled() { q35_abort(); return; }
    q35_pos += 1; q35_ms += platform_now_ms() - start; q35_count += 1;
}
fn qwen35_argmax() -> u64 {
    var best: u64 = 0;
    for i in 1..q35_vocab { if q35_logits[i] > q35_logits[best] { best = i; } }
    return best;
}
fn qwen35_logit(i: u64) -> f32 { return q35_logits[i]; }
fn qwen35_forward_cancelled() -> bool { return q35_cancelled; }
fn qwen35_forward_ms() -> u64 { return q35_ms; }
fn qwen35_forward_count() -> u64 { return q35_count; }
fn qwen35_ctx() -> u64 { return q35_ctx; }
fn qwen35_vocab() -> u64 { return q35_vocab; }
fn qwen35_dim() -> u64 { return q35_dim; }
fn qwen35_n_layers() -> u64 { return q35_layers; }
