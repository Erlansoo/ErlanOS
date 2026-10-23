// Transformers Llama / Qwen2 (RMSNorm, RoPE, GQA, SwiGLU) con caché KV, en Erlan.
// Pesos Q8_0 / F16 / F32 leídos directamente del GGUF mapeado. Un token por paso.

extern c "umath.h" { fn f16_to_f32(h: u16) -> f32; fn ld_u16(a: u64) -> u16; fn ld_f32(a: u64) -> f32; }
extern c "umath.h" { fn math_cpu_avx2() -> bool; }
extern c "matmul_fast.h" {
    fn matmul_q20_fast(out: []f32, xin: []f32, w: u64, rows: u64, cols: u64, quant: []i32, scales: []f32);
}
extern c "platform.h" {
    fn platform_log(s: str);
    fn platform_now_ms() -> u64;
    fn platform_yield();
    fn platform_cancelled() -> bool;
}
extern c "arena.h" {
    fn arena_f32(n: u64) -> []f32;
    fn arena_u64(n: u64) -> []u64;
    fn arena_u32(n: u64) -> []u32;
    fn arena_used() -> u64;
    fn arena_size() -> u64;
}
extern c "mathx.h" {
    fn mx_exp(x: f32) -> f32;
    fn mx_pow(b: f32, e: f32) -> f32;
    fn mx_sin(x: f32) -> f32;
    fn mx_cos(x: f32) -> f32;
    fn mx_silu(x: f32) -> f32;
    fn mx_sqrt(x: f32) -> f32;
}
extern c "gguf.h" {
    fn gguf_find(name: []u8) -> u64;
    fn gguf_tensor_addr(i: u64) -> u64;
    fn gguf_tensor_type(i: u64) -> u32;
    fn gguf_tensor_ne0(i: u64) -> u64;
    fn gguf_tensor_ne1(i: u64) -> u64;
    fn gguf_tensor_ndim(i: u64) -> u64;
    fn gguf_architecture() -> u64;
    fn gguf_n_tensors() -> u64;
    fn gguf_n_layers() -> u64;
    fn gguf_dim() -> u64;
    fn gguf_hidden() -> u64;
    fn gguf_n_heads() -> u64;
    fn gguf_n_kv_heads() -> u64;
    fn gguf_rope_dim() -> u64;
    fn gguf_rope_base() -> f32;
    fn gguf_eps() -> f32;
    fn gguf_ctx_len() -> u64;
    fn gguf_vocab_n() -> u64;
}
extern c "qwen35.h" {
    fn qwen35_init() -> bool;
    fn qwen35_step(token: u64, pos: u64, need_logits: bool);
    fn qwen35_reset_context(); fn qwen35_argmax() -> u64;
    fn qwen35_logit(i: u64) -> f32; fn qwen35_forward_cancelled() -> bool;
    fn qwen35_forward_ms() -> u64; fn qwen35_forward_count() -> u64;
    fn qwen35_ctx() -> u64; fn qwen35_vocab() -> u64;
    fn qwen35_dim() -> u64; fn qwen35_n_layers() -> u64;
}

const GGML_F32: u32 = 0;
const GGML_F16: u32 = 1;
const GGML_Q8_0: u32 = 8;
const MAX_CTX: u64 = 512;
const MAX_MATMUL_COLS: u64 = 16384;

// índices de pesos por capa
const W_ATTN_NORM: u64 = 0;
const W_Q: u64 = 1;
const W_K: u64 = 2;
const W_V: u64 = 3;
const W_O: u64 = 4;
const W_FFN_NORM: u64 = 5;
const W_GATE: u64 = 6;
const W_DOWN: u64 = 7;
const W_UP: u64 = 8;

var n_layers: u64 = 0;
var dim: u64 = 0;
var hidden: u64 = 0;
var n_heads: u64 = 0;
var n_kv_heads: u64 = 0;
var head_size: u64 = 0;
var kv_dim: u64 = 0;
var vocab: u64 = 0;
var ctx: u64 = 0;
var eps: f32 = 0.00001;
var architecture: u64 = 0;

var w_addr: []u64;      // n_layers * 9
var w_type: []u32;
var qkv_bias: []u64;   // n_layers * 3; 0 = bias ausente
var embd_addr: u64 = 0;
var embd_type: u32 = 0;
var out_norm_addr: u64 = 0;
var output_addr: u64 = 0;
var output_type: u32 = 0;

var x: []f32;
var xb: []f32;
var xb2: []f32;
var hb: []f32;
var hb2: []f32;
var q: []f32;
var k: []f32;
var v: []f32;
var att: []f32;
var logits: []f32;
var kcache: []f32;      // n_layers * ctx * kv_dim
var vcache: []f32;
var rope_cos: []f32;    // ctx * head_size/2
var rope_sin: []f32;
// Un solo forward por servicio: scratch persistente evita crecer la pila de 64 KiB.
var quant_scratch: [16384]i32;
var scale_scratch: [512]f32;
var matmul_fast_state: u64 = 0; // 0 unknown, 1 portable, 2 AVX2+OS context support

var forward_ms: u64 = 0;
var forward_count: u64 = 0;
var forward_cancelled: bool = false;

fn name_layer(buf: []u8, layer: u64, suffix: str) -> u64 {
    var n: u64 = 0;
    buf[0] = 98; buf[1] = 108; buf[2] = 107; buf[3] = 46;   // "blk."
    n = 4;
    if layer >= 10 { buf[n] = 48 + ((layer / 10) as u8); n += 1; }
    buf[n] = 48 + ((layer % 10) as u8);
    n += 1;
    buf[n] = 46;
    n += 1;
    unsafe { var i: u64 = 0; while suffix[i] != 0 { buf[n] = suffix[i]; n += 1; i += 1; } }
    return n;
}

fn name_plain(buf: []u8, s: str) -> u64 {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { buf[n] = s[n]; n += 1; } }
    return n;
}

fn tensor_valid(t: u64, cols: u64, rows: u64, vector: bool, f32_only: bool) -> bool {
    let dtype = gguf_tensor_type(t);
    if gguf_tensor_ne0(t) != cols || gguf_tensor_ne1(t) != rows {
        platform_log("llama: dimensiones de tensor incompatibles"); return false;
    }
    if (vector && gguf_tensor_ndim(t) != 1) || (!vector && gguf_tensor_ndim(t) != 2) {
        platform_log("llama: rango de tensor incompatible"); return false;
    }
    if f32_only {
        if dtype != GGML_F32 { platform_log("llama: norma/bias requiere F32"); return false; }
    } else {
        if dtype != GGML_F32 && dtype != GGML_F16 && dtype != GGML_Q8_0 {
            platform_log("llama: tipo de peso no soportado"); return false;
        }
        if dtype == GGML_Q8_0 && cols % 32 != 0 {
            platform_log("llama: ancho Q8_0 no divisible entre 32"); return false;
        }
    }
    return true;
}

fn load_weight(layer: u64, which: u64, suffix: str, cols: u64, rows: u64, norm: bool) -> bool {
    var buf: [64]u8;
    let n = name_layer(buf[..], layer, suffix);
    let t = gguf_find(buf[0..n]);
    if t == gguf_n_tensors() { platform_log("llama: falta tensor de capa"); return false; }
    if !tensor_valid(t, cols, rows, norm, norm) { platform_log(suffix); return false; }
    w_addr[layer * 9 + which] = gguf_tensor_addr(t);
    w_type[layer * 9 + which] = gguf_tensor_type(t);
    return true;
}

fn load_bias(layer: u64, which: u64, suffix: str, len: u64) -> bool {
    var buf: [64]u8;
    let n = name_layer(buf[..], layer, suffix);
    let t = gguf_find(buf[0..n]);
    if t == gguf_n_tensors() { return true; }
    if !tensor_valid(t, len, 1, true, true) { platform_log(suffix); return false; }
    qkv_bias[layer * 3 + which] = gguf_tensor_addr(t);
    return true;
}

fn llama_init() -> bool {
    architecture = gguf_architecture();
    if architecture == 3 { return qwen35_init(); }
    if architecture != 1 && architecture != 2 { platform_log("llama: arquitectura no soportada"); return false; }
    n_layers = gguf_n_layers();
    dim = gguf_dim();
    hidden = gguf_hidden();
    n_heads = gguf_n_heads();
    n_kv_heads = gguf_n_kv_heads();
    vocab = gguf_vocab_n();
    eps = gguf_eps();
    ctx = gguf_ctx_len();
    if ctx > MAX_CTX { ctx = MAX_CTX; }
    if n_layers == 0 || n_layers > 99 || dim == 0 || dim > MAX_MATMUL_COLS || hidden == 0 || hidden > 16384 ||
       n_heads == 0 || n_heads > 128 || n_kv_heads == 0 || n_kv_heads > n_heads || vocab == 0 || vocab > 262144 || ctx == 0 {
        platform_log("llama: hiperparametros invalidos"); return false;
    }
    if dim % n_heads != 0 || n_heads % n_kv_heads != 0 { platform_log("llama: dimensiones GQA invalidas"); return false; }
    head_size = dim / n_heads;
    kv_dim = n_kv_heads * head_size;
    if head_size % 2 != 0 || gguf_rope_dim() != head_size || !(gguf_rope_base() > 0.0) || !(eps > 0.0) {
        platform_log("llama: RoPE/RMS no soportado"); return false;
    }
    let needed = n_layers * 132 + 4 * (4 * dim + 2 * hidden + 2 * kv_dim + n_heads * ctx + vocab +
                 2 * n_layers * ctx * kv_dim + ctx * head_size) + 2048;
    if arena_used() > arena_size() || needed > arena_size() - arena_used() {
        platform_log("llama: arena insuficiente para activaciones/KV"); return false;
    }
    forward_ms = 0;
    forward_count = 0;
    forward_cancelled = false;

    w_addr = arena_u64(n_layers * 9);
    w_type = arena_u32(n_layers * 9);
    qkv_bias = arena_u64(n_layers * 3);
    for l in 0..n_layers {
        if !load_weight(l, W_ATTN_NORM, "attn_norm.weight", dim, 1, true) { return false; }
        if !load_weight(l, W_Q, "attn_q.weight", dim, dim, false) { return false; }
        if !load_weight(l, W_K, "attn_k.weight", dim, kv_dim, false) { return false; }
        if !load_weight(l, W_V, "attn_v.weight", dim, kv_dim, false) { return false; }
        if !load_weight(l, W_O, "attn_output.weight", dim, dim, false) { return false; }
        if !load_weight(l, W_FFN_NORM, "ffn_norm.weight", dim, 1, true) { return false; }
        if !load_weight(l, W_GATE, "ffn_gate.weight", dim, hidden, false) { return false; }
        if !load_weight(l, W_DOWN, "ffn_down.weight", hidden, dim, false) { return false; }
        if !load_weight(l, W_UP, "ffn_up.weight", dim, hidden, false) { return false; }
        if architecture == 2 {
            if !load_bias(l, 0, "attn_q.bias", dim) { return false; }
            if !load_bias(l, 1, "attn_k.bias", kv_dim) { return false; }
            if !load_bias(l, 2, "attn_v.bias", kv_dim) { return false; }
        }
    }
    var buf: [64]u8;
    var n = name_plain(buf[..], "token_embd.weight");
    var t = gguf_find(buf[0..n]);
    if t == gguf_n_tensors() { platform_log("llama: falta token_embd"); return false; }
    if !tensor_valid(t, dim, vocab, false, false) { platform_log("token_embd.weight"); return false; }
    embd_addr = gguf_tensor_addr(t);
    embd_type = gguf_tensor_type(t);
    n = name_plain(buf[..], "output_norm.weight");
    t = gguf_find(buf[0..n]);
    if t == gguf_n_tensors() { platform_log("llama: falta output_norm"); return false; }
    if !tensor_valid(t, dim, 1, true, true) { platform_log("output_norm.weight"); return false; }
    out_norm_addr = gguf_tensor_addr(t);
    n = name_plain(buf[..], "output.weight");
    t = gguf_find(buf[0..n]);
    if t == gguf_n_tensors() { output_addr = embd_addr; output_type = embd_type; }
    else {
        if !tensor_valid(t, dim, vocab, false, false) { platform_log("output.weight"); return false; }
        output_addr = gguf_tensor_addr(t); output_type = gguf_tensor_type(t);
    }

    x = arena_f32(dim);
    xb = arena_f32(dim);
    xb2 = arena_f32(dim);
    hb = arena_f32(hidden);
    hb2 = arena_f32(hidden);
    q = arena_f32(dim);
    k = arena_f32(kv_dim);
    v = arena_f32(kv_dim);
    att = arena_f32(n_heads * ctx);
    logits = arena_f32(vocab);
    kcache = arena_f32(n_layers * ctx * kv_dim);
    vcache = arena_f32(n_layers * ctx * kv_dim);

    // theta_i = pos * base^(-2i/rope_dim). Layout: Llama adyacente; Qwen2 NEOX.
    let half = head_size / 2;
    rope_cos = arena_f32(ctx * half);
    rope_sin = arena_f32(ctx * half);
    let base = gguf_rope_base();
    let rd = gguf_rope_dim() as f32;
    for i in 0..half {
        let freq = mx_pow(base, -((2 * i) as f32) / rd);
        for p in 0..ctx {
            let ang = (p as f32) * freq;
            rope_cos[p * half + i] = mx_cos(ang);
            rope_sin[p * half + i] = mx_sin(ang);
        }
    }
    return true;
}

fn llama_ctx() -> u64 { if architecture == 3 { return qwen35_ctx(); } return ctx; }
fn llama_vocab() -> u64 { if architecture == 3 { return qwen35_vocab(); } return vocab; }

// out[r] = sum_c W[r][c] * x[c]; W con `rows` filas de `cols` elementos, tipo ggml.
fn matmul_float(out: []f32, xin: []f32, w: u64, wtype: u32, rows: u64, cols: u64) {
    unsafe {
        let xp = xin.ptr;
        if wtype == GGML_Q8_0 {
            let nb = cols / 32;
            let row_bytes = nb * 34;
            for r in 0..rows {
                var sum: f32 = 0.0;
                var bp = (w + r * row_bytes) as ptr<u8>;
                for b in 0..nb {
                    let d = f16_to_f32(ld_u16(bp as u64));
                    let qs = (bp + 2) as ptr<i8>;
                    let xo = xp + b * 32;
                    // Cuatro cadenas independientes permiten a LLVM usar SSE2
                    // sin fast-math ni cambiar la representación Q8_0.
                    var a0: f32 = 0.0;
                    var a1: f32 = 0.0;
                    var a2: f32 = 0.0;
                    var a3: f32 = 0.0;
                    for i in 0..8 {
                        let j = i * 4;
                        a0 += (qs[j] as f32) * xo[j];
                        a1 += (qs[j + 1] as f32) * xo[j + 1];
                        a2 += (qs[j + 2] as f32) * xo[j + 2];
                        a3 += (qs[j + 3] as f32) * xo[j + 3];
                    }
                    sum += d * ((a0 + a1) + (a2 + a3));
                    bp = bp + 34;
                }
                out[r] = sum;
            }
        } else if wtype == GGML_F32 {
            for r in 0..rows {
                let wp = (w + r * cols * 4) as ptr<f32>;
                var sum: f32 = 0.0;
                for c in 0..cols { sum += wp[c] * xp[c]; }
                out[r] = sum;
            }
        } else if wtype == GGML_F16 {
            for r in 0..rows {
                let wp = (w + r * cols * 2) as ptr<u16>;
                var sum: f32 = 0.0;
                for c in 0..cols { sum += f16_to_f32(wp[c]) * xp[c]; }
                out[r] = sum;
            }
        } else {
            platform_log("llama: tipo de tensor no soportado");
        }
    }
}

// Q8_0 usa activaciones Q20 para reducir operaciones flotantes emuladas en TCG.
// Otros tipos o anchos no compatibles conservan el producto flotante anterior.
fn matmul_fast_enabled() -> bool {
    if matmul_fast_state == 0 {
        matmul_fast_state = 1;
        if math_cpu_avx2() { matmul_fast_state = 2; platform_log("matmul: AVX2"); }
        else { platform_log("matmul: portable"); }
    }
    return matmul_fast_state == 2;
}

fn matmul(out: []f32, xin: []f32, w: u64, wtype: u32, rows: u64, cols: u64) {
    if wtype == GGML_Q8_0 && cols <= MAX_MATMUL_COLS && cols % 32 == 0 && matmul_fast_enabled() {
        matmul_q20_fast(out, xin, w, rows, cols, quant_scratch[..], scale_scratch[..]);
        return;
    }
    matmul_q20(out, xin, w, wtype, rows, cols);
}

// Cuantización de activaciones por bloque sin cambiar los pesos del GGUF.
// 32 * 128 * 524287 < 2^31: el producto escalar entero no desborda i32.
fn matmul_q20(out: []f32, xin: []f32, w: u64, wtype: u32, rows: u64, cols: u64) {
    if wtype != GGML_Q8_0 || cols > MAX_MATMUL_COLS || cols % 32 != 0 {
        matmul_float(out, xin, w, wtype, rows, cols);
        return;
    }
    let nb = cols / 32;
    unsafe {
        let xp = xin.ptr;
        for b in 0..nb {
            let off = b * 32;
            var mx: f32 = 0.0;
            for i in 0..32 {
                var a = xp[off + i];
                if a < 0.0 { a = -a; }
                if a > mx { mx = a; }
            }
            scale_scratch[b] = mx / 524287.0;
            for i in 0..32 {
                var q: i32 = 0;
                if mx > 0.0 {
                    let scaled = (xp[off + i] / mx) * 524287.0;
                    if scaled >= 0.0 { q = (scaled + 0.5) as i32; }
                    else { q = (scaled - 0.5) as i32; }
                    if q > 524287 { q = 524287; }
                    if q < -524287 { q = -524287; }
                }
                quant_scratch[off + i] = q;
            }
        }
        let row_bytes = nb * 34;
        for r in 0..rows {
            var sum: f32 = 0.0;
            var bp = (w + r * row_bytes) as ptr<u8>;
            for b in 0..nb {
                let d = f16_to_f32(ld_u16(bp as u64));
                let qs = (bp + 2) as ptr<i8>;
                let off = b * 32;
                var acc: i32 = 0;
                for i in 0..32 { acc += (qs[i] as i32) * quant_scratch[off + i]; }
                sum += (d * scale_scratch[b]) * (acc as f32);
                bp = bp + 34;
            }
            out[r] = sum;
        }
    }
}

// Fila `row` de una matriz (rows x cols) dequantizada a out.
fn dequant_row(out: []f32, w: u64, wtype: u32, row: u64, cols: u64) {
    unsafe {
        if wtype == GGML_Q8_0 {
            let nb = cols / 32;
            var bp = (w + row * nb * 34) as ptr<u8>;
            for b in 0..nb {
                let d = f16_to_f32(ld_u16(bp as u64));
                let qs = (bp + 2) as ptr<i8>;
                for i in 0..32 { out[b * 32 + i] = d * (qs[i] as f32); }
                bp = bp + 34;
            }
        } else if wtype == GGML_F32 {
            let wp = (w + row * cols * 4) as ptr<f32>;
            for c in 0..cols { out[c] = wp[c]; }
        } else if wtype == GGML_F16 {
            let wp = (w + row * cols * 2) as ptr<u16>;
            for c in 0..cols { out[c] = f16_to_f32(wp[c]); }
        }
    }
}

fn rmsnorm(out: []f32, xin: []f32, weight: u64, n: u64) {
    var ss: f32 = 0.0;
    for i in 0..n { ss += xin[i] * xin[i]; }
    ss = ss / (n as f32) + eps;
    let inv = 1.0 / mx_sqrt(ss);
    unsafe {
        let wp = weight as ptr<f32>;
        for i in 0..n { out[i] = wp[i] * (xin[i] * inv); }
    }
}

fn rope(vec: []f32, heads: u64, pos: u64) {
    let half = head_size / 2;
    for h in 0..heads {
        let o = h * head_size;
        for i in 0..half {
            let c = rope_cos[pos * half + i];
            let s = rope_sin[pos * half + i];
            var a = o + 2 * i;
            var b = a + 1;
            // Qwen2 GGUF conserva el orden HF: pares separados por media cabeza.
            if architecture == 2 { a = o + i; b = a + half; }
            let x0 = vec[a];
            let x1 = vec[b];
            vec[a] = x0 * c - x1 * s;
            vec[b] = x0 * s + x1 * c;
        }
    }
}

fn add_bias(vec: []f32, addr: u64, len: u64) {
    if addr == 0 { return; }
    unsafe {
        let bias = addr as ptr<f32>;
        for i in 0..len { vec[i] += bias[i]; }
    }
}

fn softmax(s: []f32, off: u64, n: u64) {
    var mx = s[off];
    for i in 1..n { if s[off + i] > mx { mx = s[off + i]; } }
    var sum: f32 = 0.0;
    for i in 0..n { let e = mx_exp(s[off + i] - mx); s[off + i] = e; sum += e; }
    let inv = 1.0 / sum;
    for i in 0..n { s[off + i] = s[off + i] * inv; }
}

fn wa(l: u64, which: u64) -> u64 { return w_addr[l * 9 + which]; }
fn wt(l: u64, which: u64) -> u32 { return w_type[l * 9 + which]; }

// Paso compartido: prefill actualiza todas las capas/KV sin proyectar el vocabulario.
fn llama_step(token: u64, pos: u64, need_logits: bool) {
    if architecture == 3 { qwen35_step(token, pos, need_logits); return; }
    forward_cancelled = false;
    if platform_cancelled() { forward_cancelled = true; return; }
    let t0 = platform_now_ms();
    dequant_row(x, embd_addr, embd_type, token, dim);
    let scale = 1.0 / mx_sqrt(head_size as f32);
    let group = n_heads / n_kv_heads;

    for l in 0..n_layers {
        if platform_cancelled() { forward_cancelled = true; return; }
        rmsnorm(xb, x, wa(l, W_ATTN_NORM), dim);
        matmul(q, xb, wa(l, W_Q), wt(l, W_Q), dim, dim);
        matmul(k, xb, wa(l, W_K), wt(l, W_K), kv_dim, dim);
        matmul(v, xb, wa(l, W_V), wt(l, W_V), kv_dim, dim);
        if architecture == 2 {
            add_bias(q, qkv_bias[l * 3], dim);
            add_bias(k, qkv_bias[l * 3 + 1], kv_dim);
            add_bias(v, qkv_bias[l * 3 + 2], kv_dim);
        }
        rope(q, n_heads, pos);
        rope(k, n_kv_heads, pos);

        let cbase = l * ctx * kv_dim + pos * kv_dim;
        for i in 0..kv_dim { kcache[cbase + i] = k[i]; vcache[cbase + i] = v[i]; }

        for h in 0..n_heads {
            let qo = h * head_size;
            let kvh = h / group;
            let ao = h * ctx;
            for t in 0..pos + 1 {
                let ko = l * ctx * kv_dim + t * kv_dim + kvh * head_size;
                var s: f32 = 0.0;
                for i in 0..head_size { s += q[qo + i] * kcache[ko + i]; }
                att[ao + t] = s * scale;
            }
            softmax(att, ao, pos + 1);
            for i in 0..head_size { xb[qo + i] = 0.0; }
            for t in 0..pos + 1 {
                let vo = l * ctx * kv_dim + t * kv_dim + kvh * head_size;
                let a = att[ao + t];
                for i in 0..head_size { xb[qo + i] += a * vcache[vo + i]; }
            }
        }

        matmul(xb2, xb, wa(l, W_O), wt(l, W_O), dim, dim);
        for i in 0..dim { x[i] += xb2[i]; }

        rmsnorm(xb, x, wa(l, W_FFN_NORM), dim);
        matmul(hb, xb, wa(l, W_GATE), wt(l, W_GATE), hidden, dim);
        matmul(hb2, xb, wa(l, W_UP), wt(l, W_UP), hidden, dim);
        for i in 0..hidden { hb[i] = mx_silu(hb[i]) * hb2[i]; }
        matmul(xb, hb, wa(l, W_DOWN), wt(l, W_DOWN), dim, hidden);
        for i in 0..dim { x[i] += xb[i]; }
        platform_yield();
    }

    if platform_cancelled() { forward_cancelled = true; return; }
    if need_logits {
        rmsnorm(x, x, out_norm_addr, dim);
        matmul(logits, x, output_addr, output_type, vocab, dim);
    }
    if platform_cancelled() { forward_cancelled = true; return; }
    forward_ms += platform_now_ms() - t0;
    forward_count += 1;
}

// Paso con logits válidos para seleccionar el siguiente token.
fn llama_forward(token: u64, pos: u64) { llama_step(token, pos, true); }

// Sólo para tokens cuyo resultado inmediato no se consulta. Conserva los logits
// anteriores: el último token del prompt siempre debe usar llama_forward.
fn llama_prefill(token: u64, pos: u64) { llama_step(token, pos, false); }

fn llama_argmax() -> u64 {
    if architecture == 3 { return qwen35_argmax(); }
    var best: u64 = 0;
    var bv = logits[0];
    for i in 1..vocab { if logits[i] > bv { bv = logits[i]; best = i; } }
    return best;
}

fn llama_logit(i: u64) -> f32 { if architecture == 3 { return qwen35_logit(i); } return logits[i]; }
fn llama_forward_ms() -> u64 { if architecture == 3 { return qwen35_forward_ms(); } return forward_ms; }
fn llama_forward_count() -> u64 { if architecture == 3 { return qwen35_forward_count(); } return forward_count; }
// Tras true, el llamador descarta logits y cualquier prefijo KV reutilizable.
fn llama_forward_cancelled() -> bool { if architecture == 3 { return qwen35_forward_cancelled(); } return forward_cancelled; }
fn llama_dim() -> u64 { if architecture == 3 { return qwen35_dim(); } return dim; }
fn llama_n_layers() -> u64 { if architecture == 3 { return qwen35_n_layers(); } return n_layers; }
fn llama_reset_context() {
    if architecture == 3 { qwen35_reset_context(); }
    forward_cancelled = false;
}
