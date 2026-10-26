// Parser GGUF v3 (formato de llama.cpp) en Erlan. Lee metadatos, localiza los arrays
// del tokenizer y la tabla de tensores. No copia los pesos: trabaja sobre el archivo
// mapeado en memoria (módulo de Limine en ErlanOS, buffer en host).

import umath;
import platform;
import arena;

const GGUF_MAGIC: u32 = 0x46554747;   // "GGUF" little-endian
const V_STR: u32 = 8;
const V_ARR: u32 = 9;

// Tipos de tensor ggml
const GGML_F32: u32 = 0;
const GGML_F16: u32 = 1;
const GGML_Q8_0: u32 = 8;

var architecture: u64 = 0;
var invalid: bool = false;
var meta_key: []u64;
var meta_len: []u64;
var meta_pos: []u64;
var meta_type: []u32;
var t_ndim: []u32;
var t_ne2: []u64;
var t_ne3: []u64;
var base: u64 = 0;
var size: u64 = 0;
var n_tensors: u64 = 0;
var n_kv: u64 = 0;
var alignment: u64 = 32;
var data_start: u64 = 0;

// hiperparámetros (llama)
var n_layers: u64 = 0;
var dim: u64 = 0;
var hidden: u64 = 0;
var n_heads: u64 = 0;
var n_kv_heads: u64 = 0;
var rope_dim: u64 = 0;
var rope_base: f32 = 10000.0;
var eps: f32 = 0.00001;
var ctx_len: u64 = 2048;
var vocab_n: u64 = 0;
var bos: u64 = 1;
var eos: u64 = 2;

// arrays del tokenizer: dirección del primer string y cantidad
var tokens_off: u64 = 0;
var tokens_n: u64 = 0;
var token_kinds_off: u64 = 0;
var merges_off: u64 = 0;
var merges_n: u64 = 0;

// tabla de tensores
var t_name_off: []u64;
var t_name_len: []u64;
var t_ne0: []u64;
var t_ne1: []u64;
var t_type: []u32;
var t_off: []u64;

fn in_file(p: u64, n: u64) -> bool {
    if p < base || p - base > size || n > size - (p - base) { invalid = true; return false; }
    return true;
}
fn scalar_size(t: u32) -> u64 {
    if t == 0 || t == 1 || t == 7 { return 1; }
    if t == 2 || t == 3 { return 2; }
    if t == 4 || t == 5 || t == 6 { return 4; }
    if t == 10 || t == 11 || t == 12 { return 8; }
    invalid = true; return 0;
}
fn skip_value(t: u32, pos: u64, depth: u64) -> u64 {
    if invalid || depth > 8 { invalid = true; return pos; }
    if t == V_STR {
        if !in_file(pos, 8) { return pos; }
        let n = ld_u64(pos);
        if !in_file(pos + 8, n) { return pos; }
        return pos + 8 + n;
    }
    if t == V_ARR {
        if !in_file(pos, 12) { return pos; }
        let et = ld_u32(pos); let n = ld_u64(pos + 4); var p = pos + 12;
        if n > size { invalid = true; return p; }
        if et == V_STR || et == V_ARR {
            for i in 0..n { p = skip_value(et, p, depth + 1); if invalid { return p; } }
            return p;
        }
        let width = scalar_size(et);
        if invalid || width == 0 || n > size / width { invalid = true; return p; }
        if !in_file(p, n * width) { return p; }
        return p + n * width;
    }
    let width = scalar_size(t);
    if !in_file(pos, width) { return pos; }
    return pos + width;
}

fn key_is(off: u64, len: u64, s: str) -> bool {
    var n: u64 = 0;
    unsafe {
        let p = off as ptr<u8>;
        while n < len {
            if s[n] == 0 { return false; }
            if s[n] != p[n] { return false; }
            n += 1;
        }
        return s[n] == 0;
    }
    return false;
}

fn read_u64_any(t: u32, pos: u64) -> u64 {
    if t == 4 || t == 5 { return ld_u32(pos) as u64; }
    if t == 10 || t == 11 { return ld_u64(pos); }
    if t == 2 || t == 3 { return ld_u16(pos) as u64; }
    if t == 0 || t == 1 { var v: u64 = 0; unsafe { let p = pos as ptr<u8>; v = p[0] as u64; } return v; }
    return 0;
}

// Generic typed accessors also expose the hybrid model's SSM metadata.
fn meta_find(name: str) -> u64 {
    for i in 0..n_kv { if key_is(meta_key[i], meta_len[i], name) { return i; } }
    return n_kv;
}
fn gguf_meta_u64(name: str, fallback: u64) -> u64 {
    let i = meta_find(name); if i == n_kv { return fallback; }
    let t = meta_type[i];
    if t == 0 || t == 1 || t == 2 || t == 3 || t == 4 || t == 5 || t == 10 || t == 11 { return read_u64_any(t, meta_pos[i]); }
    return fallback;
}
fn gguf_meta_f32(name: str, fallback: f32) -> f32 {
    let i = meta_find(name); if i == n_kv || meta_type[i] != 6 { return fallback; }
    return ld_f32(meta_pos[i]);
}
fn gguf_meta_is(name: str, value: str) -> bool {
    let i = meta_find(name); if i == n_kv || meta_type[i] != V_STR { return false; }
    return key_is(meta_pos[i] + 8, ld_u64(meta_pos[i]), value);
}
fn arch_u64(a: str, b: str, c: str, fallback: u64) -> u64 {
    if architecture == 1 { return gguf_meta_u64(a, fallback); }
    if architecture == 2 { return gguf_meta_u64(b, fallback); }
    return gguf_meta_u64(c, fallback);
}
fn arch_f32(a: str, b: str, c: str, fallback: f32) -> f32 {
    if architecture == 1 { return gguf_meta_f32(a, fallback); }
    if architecture == 2 { return gguf_meta_f32(b, fallback); }
    return gguf_meta_f32(c, fallback);
}
fn gguf_open(b: u64, s: u64) -> bool {
    base = b; size = s; invalid = false; architecture = 0;
    tokens_n = 0; merges_n = 0; tokens_off = 0; merges_off = 0; token_kinds_off = 0;
    if s < 24 || b > 0xFFFFFFFFFFFFFFFF - s { platform_log("gguf: cabecera truncada"); return false; }
    if ld_u32(base) != GGUF_MAGIC { platform_log("gguf: magic invalido"); return false; }
    let version = ld_u32(base + 4);
    if version != 3 && version != 2 { platform_log("gguf: version no soportada"); return false; }
    n_tensors = ld_u64(base + 8); n_kv = ld_u64(base + 16);
    if n_tensors == 0 || n_tensors > 16384 || n_kv > 4096 { platform_log("gguf: tablas excesivas"); return false; }
    let needed = n_kv * 28 + n_tensors * 64 + 2048;
    if arena_used() > arena_size() || needed > arena_size() - arena_used() { platform_log("gguf: arena insuficiente"); return false; }
    meta_key = arena_u64(n_kv); meta_len = arena_u64(n_kv); meta_pos = arena_u64(n_kv); meta_type = arena_u32(n_kv);
    var pos = base + 24;
    for i in 0..n_kv {
        if !in_file(pos, 8) { break; }
        let klen = ld_u64(pos); pos += 8;
        if !in_file(pos, klen) || klen > 4096 { invalid = true; break; }
        meta_key[i] = pos; meta_len[i] = klen; pos += klen;
        if !in_file(pos, 4) { break; }
        meta_type[i] = ld_u32(pos); pos += 4; meta_pos[i] = pos;
        pos = skip_value(meta_type[i], pos, 0);
        if invalid { break; }
    }
    if invalid { platform_log("gguf: metadatos truncados/invalidos"); return false; }
    if gguf_meta_is("general.architecture", "llama") { architecture = 1; }
    else if gguf_meta_is("general.architecture", "qwen2") { architecture = 2; }
    else if gguf_meta_is("general.architecture", "qwen35") { architecture = 3; }
    else { platform_log("gguf: arquitectura no soportada"); return false; }
    n_layers = arch_u64("llama.block_count", "qwen2.block_count", "qwen35.block_count", 0);
    dim = arch_u64("llama.embedding_length", "qwen2.embedding_length", "qwen35.embedding_length", 0);
    hidden = arch_u64("llama.feed_forward_length", "qwen2.feed_forward_length", "qwen35.feed_forward_length", 0);
    n_heads = arch_u64("llama.attention.head_count", "qwen2.attention.head_count", "qwen35.attention.head_count", 0);
    n_kv_heads = arch_u64("llama.attention.head_count_kv", "qwen2.attention.head_count_kv", "qwen35.attention.head_count_kv", 0);
    rope_dim = arch_u64("llama.rope.dimension_count", "qwen2.rope.dimension_count", "qwen35.rope.dimension_count", 0);
    rope_base = arch_f32("llama.rope.freq_base", "qwen2.rope.freq_base", "qwen35.rope.freq_base", 10000.0);
    eps = arch_f32("llama.attention.layer_norm_rms_epsilon", "qwen2.attention.layer_norm_rms_epsilon", "qwen35.attention.layer_norm_rms_epsilon", 0.00001);
    ctx_len = arch_u64("llama.context_length", "qwen2.context_length", "qwen35.context_length", 512);
    if n_heads == 0 || dim == 0 || n_layers == 0 { platform_log("gguf: dimensiones invalidas"); return false; }
    if rope_dim == 0 { rope_dim = dim / n_heads; }
    bos = gguf_meta_u64("tokenizer.ggml.bos_token_id", 0); eos = gguf_meta_u64("tokenizer.ggml.eos_token_id", 0);
    var m = meta_find("tokenizer.ggml.tokens");
    if m == n_kv || meta_type[m] != V_ARR || ld_u32(meta_pos[m]) != V_STR { platform_log("gguf: falta vocabulario"); return false; }
    tokens_n = ld_u64(meta_pos[m] + 4); tokens_off = meta_pos[m] + 12; vocab_n = tokens_n;
    m = meta_find("tokenizer.ggml.token_type");
    if m < n_kv && meta_type[m] == V_ARR {
        if ld_u32(meta_pos[m]) == 5 && ld_u64(meta_pos[m] + 4) == tokens_n { token_kinds_off = meta_pos[m] + 12; }
    }
    m = meta_find("tokenizer.ggml.merges");
    if m == n_kv || meta_type[m] != V_ARR || ld_u32(meta_pos[m]) != V_STR { platform_log("gguf: faltan merges"); return false; }
    merges_n = ld_u64(meta_pos[m] + 4); merges_off = meta_pos[m] + 12;
    alignment = gguf_meta_u64("general.alignment", 32);
    if alignment == 0 || alignment > 4096 || (alignment & (alignment - 1)) != 0 { platform_log("gguf: alineacion invalida"); return false; }
    t_name_off = arena_u64(n_tensors); t_name_len = arena_u64(n_tensors);
    t_ne0 = arena_u64(n_tensors); t_ne1 = arena_u64(n_tensors); t_ne2 = arena_u64(n_tensors); t_ne3 = arena_u64(n_tensors);
    t_ndim = arena_u32(n_tensors); t_type = arena_u32(n_tensors); t_off = arena_u64(n_tensors);
    for i in 0..n_tensors {
        if !in_file(pos, 8) { break; }
        let nlen = ld_u64(pos); pos += 8;
        if !in_file(pos, nlen) || nlen > 4096 { invalid = true; break; }
        t_name_off[i] = pos; t_name_len[i] = nlen; pos += nlen;
        if !in_file(pos, 4) { break; }
        let ndim = ld_u32(pos) as u64; pos += 4;
        if ndim == 0 || ndim > 4 || !in_file(pos, ndim * 8 + 12) { invalid = true; break; }
        t_ndim[i] = ndim as u32;
        t_ne0[i] = ld_u64(pos); t_ne1[i] = 1; t_ne2[i] = 1; t_ne3[i] = 1;
        if ndim > 1 { t_ne1[i] = ld_u64(pos + 8); }
        if ndim > 2 { t_ne2[i] = ld_u64(pos + 16); }
        if ndim > 3 { t_ne3[i] = ld_u64(pos + 24); }
        pos += 8 * ndim; t_type[i] = ld_u32(pos); pos += 4; t_off[i] = ld_u64(pos); pos += 8;
    }
    if invalid { platform_log("gguf: tabla de tensores invalida"); return false; }
    let aligned = ((pos - base + alignment - 1) / alignment) * alignment;
    if aligned > size { platform_log("gguf: faltan pesos"); return false; }
    data_start = base + aligned;
    for i in 0..n_tensors {
        var dims: [4]u64; dims[0] = t_ne0[i]; dims[1] = t_ne1[i]; dims[2] = t_ne2[i]; dims[3] = t_ne3[i];
        var elements: u64 = 1;
        for d in 0..4 {
            if dims[d] == 0 || dims[d] > size || elements > size / dims[d] { platform_log("gguf: dimensiones excesivas"); return false; }
            elements = elements * dims[d];
        }
        var bytes: u64 = 0;
        if t_type[i] == GGML_F32 { if elements > size / 4 { return false; } bytes = elements * 4; }
        else if t_type[i] == GGML_F16 || t_type[i] == 30 { if elements > size / 2 { return false; } bytes = elements * 2; }
        else if t_type[i] == GGML_Q8_0 { if t_ne0[i] % 32 != 0 { return false; } bytes = (elements / 32) * 34; }
        else { platform_log("gguf: cuantizacion no soportada (usar Q8_0)"); return false; }
        if t_off[i] > size - aligned || bytes > size - aligned - t_off[i] { platform_log("gguf: pesos truncados"); return false; }
    }
    return true;
}

// Busca un tensor por nombre (bytes). Devuelve n_tensors si no existe.
fn gguf_find(name: []u8) -> u64 {
    for i in 0..n_tensors {
        if t_name_len[i] == name.len {
            var eq = true;
            unsafe {
                let p = t_name_off[i] as ptr<u8>;
                for k in 0..name.len { if p[k] != name[k] { eq = false; } }
            }
            if eq { return i; }
        }
    }
    return n_tensors;
}

fn gguf_tensor_addr(i: u64) -> u64 { return data_start + t_off[i]; }
fn gguf_tensor_type(i: u64) -> u32 { return t_type[i]; }
fn gguf_tensor_ne0(i: u64) -> u64 { return t_ne0[i]; }
fn gguf_tensor_ne1(i: u64) -> u64 { return t_ne1[i]; }
fn gguf_n_tensors() -> u64 { return n_tensors; }

fn gguf_n_layers() -> u64 { return n_layers; }
fn gguf_dim() -> u64 { return dim; }
fn gguf_hidden() -> u64 { return hidden; }
fn gguf_n_heads() -> u64 { return n_heads; }
fn gguf_n_kv_heads() -> u64 { return n_kv_heads; }
fn gguf_rope_dim() -> u64 { return rope_dim; }
fn gguf_rope_base() -> f32 { return rope_base; }
fn gguf_eps() -> f32 { return eps; }
fn gguf_ctx_len() -> u64 { return ctx_len; }
fn gguf_vocab_n() -> u64 { return vocab_n; }
fn gguf_bos() -> u64 { return bos; }
fn gguf_eos() -> u64 { return eos; }
fn gguf_tokens_off() -> u64 { return tokens_off; }
fn gguf_tokens_n() -> u64 { return tokens_n; }
fn gguf_merges_off() -> u64 { return merges_off; }
fn gguf_merges_n() -> u64 { return merges_n; }
fn gguf_token_kind(id: u64) -> u64 {
    if token_kinds_off == 0 || id >= tokens_n { return 1; }
    return ld_u32(token_kinds_off + id * 4) as u64;
}

fn gguf_architecture() -> u64 { return architecture; }
fn gguf_model_name() -> []u8 {
    var result: []u8;
    var addr = base; var len: u64 = 0;
    let i = meta_find("general.name");
    if i < n_kv && meta_type[i] == V_STR { addr = meta_pos[i] + 8; len = ld_u64(meta_pos[i]); }
    if len > 160 { len = 160; }
    unsafe { let p = addr as ptr<u8>; result = p[0..len]; }
    return result;
}
fn gguf_tensor_ndim(i: u64) -> u64 { return t_ndim[i] as u64; }
fn gguf_tensor_ne2(i: u64) -> u64 { return t_ne2[i]; }
fn gguf_tensor_ne3(i: u64) -> u64 { return t_ne3[i]; }
