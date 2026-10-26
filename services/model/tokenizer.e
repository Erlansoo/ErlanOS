// Tokenizer BPE a nivel de bytes (estilo GPT-2, como usa SmolLM2) en Erlan.
// Tablas hash abiertas sobre los strings del GGUF; sin copiar el vocabulario.

import umath;
import platform;
import arena;
import gguf;

import unicode;
var table_size: u64 = 0;
var table_mask: u64 = 0;
var pre_type: u64 = 0; // SmolLM=1, Qwen2=2, Qwen3.5=3
const NO_TOKEN: u64 = 0xFFFFFFFFFFFFFFFF;

var n_tokens: u64 = 0;
var n_merges: u64 = 0;
var tok_off: []u64;     // dirección del string de cada token
var tok_len: []u32;
var mrg_off: []u64;     // "a b"
var mrg_len: []u32;
var vocab_tab: []u32;   // hash -> id + 1
var merge_tab: []u32;   // hash -> rank + 1
var byte_to_cp: [256]u32;
var cp_to_byte: [512]u8;
var symbuf: []u8;       // texto del chunk en "unicode de bytes" (UTF-8)
var sym_off: []u32;     // símbolos BPE del chunk actual
var sym_len: []u32;
var pairbuf: []u8;
var added_ids: [512]u32;
var added_n: u64 = 0;

fn fnv(addr: u64, len: u64) -> u32 {
    var h: u32 = 2166136261;
    unsafe {
        let p = addr as ptr<u8>;
        for i in 0..len { h = (h ^ (p[i] as u32)) * 16777619; }
    }
    return h;
}

fn bytes_eq(a: u64, alen: u64, b: u64, blen: u64) -> bool {
    if alen != blen { return false; }
    unsafe {
        let p = a as ptr<u8>;
        let q = b as ptr<u8>;
        for i in 0..alen { if p[i] != q[i] { return false; } }
    }
    return true;
}

fn table_insert(tab: []u32, offs: []u64, lens: []u32, idx: u64) {
    var h = (fnv(offs[idx], lens[idx] as u64) as u64) & table_mask;
    while tab[h] != 0 { h = (h + 1) & table_mask; }
    tab[h] = (idx + 1) as u32;
}

fn table_find(tab: []u32, offs: []u64, lens: []u32, addr: u64, len: u64) -> u64 {
    var h = (fnv(addr, len) as u64) & table_mask;
    while tab[h] != 0 {
        let i = (tab[h] as u64) - 1;
        if bytes_eq(offs[i], lens[i] as u64, addr, len) { return i; }
        h = (h + 1) & table_mask;
    }
    return NO_TOKEN;
}

// Mapa byte <-> codepoint de GPT-2 (bytes imprimibles se mapean a sí mismos).
fn init_byte_map() {
    var n: u32 = 0;
    for b in 0..256 {
        let printable = (b >= 33 && b <= 126) || (b >= 161 && b <= 172) || (b >= 174 && b <= 255);
        if printable { byte_to_cp[b] = b as u32; }
        else { byte_to_cp[b] = 256 + n; n += 1; }
        cp_to_byte[byte_to_cp[b] as u64] = b as u8;
    }
}

fn tokenizer_init() -> bool {
    n_tokens = gguf_tokens_n();
    n_merges = gguf_merges_n();
    if n_tokens == 0 || n_tokens > 1048576 || n_merges > 1048576 { platform_log("tokenizer: vocabulario excesivo/vacio"); return false; }
    pre_type = 0;
    if gguf_meta_is("tokenizer.ggml.pre", "smollm") { pre_type = 1; }
    if gguf_meta_is("tokenizer.ggml.pre", "qwen2") { pre_type = 2; }
    if gguf_meta_is("tokenizer.ggml.pre", "qwen35") { pre_type = 3; }
    if pre_type == 0 { platform_log("tokenizer: pretokenizer no soportado"); return false; }
    table_size = 2;
    while table_size < n_tokens * 2 || table_size < n_merges * 2 { table_size = table_size * 2; }
    table_mask = table_size - 1;
    let needed = (n_tokens + n_merges) * 12 + table_size * 8 + 65536;
    if arena_used() > arena_size() || needed > arena_size() - arena_used() { platform_log("tokenizer: arena insuficiente"); return false; }
    tok_off = arena_u64(n_tokens);
    tok_len = arena_u32(n_tokens);
    mrg_off = arena_u64(n_merges);
    mrg_len = arena_u32(n_merges);
    vocab_tab = arena_u32(table_size);
    merge_tab = arena_u32(table_size);
    symbuf = arena_u8(8192);
    sym_off = arena_u32(4096);
    sym_len = arena_u32(4096);
    pairbuf = arena_u8(8193);
    added_n = 0;
    init_byte_map();

    var pos = gguf_tokens_off();
    for i in 0..n_tokens {
        let len = ld_u64(pos);
        tok_off[i] = pos + 8;
        tok_len[i] = len as u32;
        pos += 8 + len;
        table_insert(vocab_tab, tok_off, tok_len, i);
        // USER_DEFINED tokens (e.g. <think>) are added tokens in Qwen's
        // tokenizer. CONTROL role tokens remain explicit at the chat boundary.
        if pre_type != 1 && gguf_token_kind(i) == 4 {
            if added_n >= added_ids.len { platform_log("tokenizer: demasiados added tokens"); return false; }
            added_ids[added_n] = i as u32; added_n += 1;
        }
    }
    pos = gguf_merges_off();
    for i in 0..n_merges {
        let len = ld_u64(pos);
        mrg_off[i] = pos + 8;
        mrg_len[i] = len as u32;
        pos += 8 + len;
        table_insert(merge_tab, mrg_off, mrg_len, i);
    }
    return true;
}

fn tokenizer_n_tokens() -> u64 { return n_tokens; }

// Exact vocabulary spelling, including control tokens that ordinary BPE splits.
fn tokenizer_id(text: str) -> u64 {
    var n: u64 = 0;
    var addr: u64 = 0;
    unsafe { while text[n] != 0 { n += 1; } addr = text as u64; }
    return table_find(vocab_tab, tok_off, tok_len, addr, n);
}

fn is_letter(c: u8) -> bool { return (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c >= 128; }
fn is_digit(c: u8) -> bool { return c >= 48 && c <= 57; }
fn is_space(c: u8) -> bool { return c == 32 || (c >= 9 && c <= 13); }

// Alternativas ASCII sensibles a mayúsculas del pretokenizer SmolLM.
// Se reconocen antes de signos/palabras, sin requerir fin de palabra.
fn contraction_len(text: []u8, pos: u64) -> u64 {
    if pos + 1 >= text.len || text[pos] != 39 { return 0; }
    var c = text[pos + 1];
    if pre_type != 1 && c >= 65 && c <= 90 { c += 32; }
    if c == 115 || c == 116 || c == 109 || c == 100 { return 2; }
    if pos + 2 >= text.len { return 0; }
    var d = text[pos + 2];
    if pre_type != 1 && d >= 65 && d <= 90 { d += 32; }
    if (c == 114 && d == 101) || (c == 118 && d == 101) || (c == 108 && d == 108) { return 3; }
    return 0;
}

// Escribe el codepoint (< 0x800) en UTF-8 al final de symbuf; devuelve bytes escritos.
fn put_cp(pos: u64, cp: u32) -> u64 {
    if cp < 128 { symbuf[pos] = cp as u8; return 1; }
    symbuf[pos] = (0xC0 | (cp >> 6)) as u8;
    symbuf[pos + 1] = (0x80 | (cp & 0x3F)) as u8;
    return 2;
}

// Aplica BPE al chunk text[a..b) y agrega los ids a out. Devuelve la nueva cuenta.
fn bpe_chunk(text: []u8, a: u64, b: u64, out: []u32, count: u64) -> u64 {
    // 1. bytes -> símbolos (un codepoint por byte)
    var nsym: u64 = 0;
    var p: u64 = 0;
    for i in a..b {
        let w = put_cp(p, byte_to_cp[text[i] as u64]);
        sym_off[nsym] = p as u32;
        sym_len[nsym] = w as u32;
        p += w;
        nsym += 1;
    }
    var symaddr: u64 = 0;
    var pairaddr: u64 = 0;
    unsafe { symaddr = symbuf.ptr as u64; pairaddr = pairbuf.ptr as u64; }

    // 2. fusionar repetidamente el par con menor rango
    while nsym > 1 {
        var best: u64 = NO_TOKEN;
        var best_i: u64 = 0;
        for i in 0..nsym - 1 {
            let la = sym_len[i] as u64;
            let lb = sym_len[i + 1] as u64;
            if la + lb + 1 > pairbuf.len { continue; }
            for k in 0..la { pairbuf[k] = symbuf[(sym_off[i] as u64) + k]; }
            pairbuf[la] = 32;
            for k in 0..lb { pairbuf[la + 1 + k] = symbuf[(sym_off[i + 1] as u64) + k]; }
            let rank = table_find(merge_tab, mrg_off, mrg_len, pairaddr, la + lb + 1);
            if rank != NO_TOKEN && (best == NO_TOKEN || rank < best) { best = rank; best_i = i; }
        }
        if best == NO_TOKEN { break; }
        // los símbolos son contiguos en symbuf: fusionar = sumar longitudes
        sym_len[best_i] = sym_len[best_i] + sym_len[best_i + 1];
        for i in best_i + 1..nsym - 1 { sym_off[i] = sym_off[i + 1]; sym_len[i] = sym_len[i + 1]; }
        nsym -= 1;
    }

    // 3. símbolos -> ids
    var n = count;
    for i in 0..nsym {
        let id = table_find(vocab_tab, tok_off, tok_len, symaddr + (sym_off[i] as u64), sym_len[i] as u64);
        if id == NO_TOKEN {
            // token desconocido: emitir byte a byte
            var q = sym_off[i] as u64;
            let end = q + (sym_len[i] as u64);
            while q < end {
                var w: u64 = 1;
                if symbuf[q] >= 0xC0 { w = 2; }
                let bid = table_find(vocab_tab, tok_off, tok_len, symaddr + q, w);
                if bid != NO_TOKEN && n < out.len { out[n] = bid as u32; n += 1; }
                q += w;
            }
        } else if n < out.len {
            out[n] = id as u32;
            n += 1;
        }
    }
    return n;
}

// SmolLM: contracciones, números separados y whitespace con lookahead negativo.
// Paridad ASCII; la clasificación Unicode completa de letras/números/whitespace
// sigue pendiente (is_letter usa los bytes UTF-8 >=128 como aproximación).
fn encode_smollm(text: []u8, out: []u32) -> u64 {
    var count: u64 = 0;
    var i: u64 = 0;
    let n = text.len;
    while i < n {
        let c = text[i];
        var start = i;
        let contraction = contraction_len(text, i);
        if contraction != 0 {
            count = bpe_chunk(text, i, i + contraction, out, count);
            i += contraction;
            continue;
        }
        if is_space(c) {
            // \s+(?!\S): al final agrupa todo; ante contenido deja el último
            // whitespace para el siguiente match (un espacio puede prefijar palabra).
            var r: u64 = 0;
            while i + r < n && is_space(text[i + r]) { r += 1; }
            // La separación numérica de SmolLM sucede antes del regex: el
            // whitespace justo antes de un dígito es final de ese fragmento.
            if i + r == n || is_digit(text[i + r]) {
                count = bpe_chunk(text, i, i + r, out, count);
                i += r;
                continue;
            }
            if r > 1 { count = bpe_chunk(text, i, i + r - 1, out, count); }
            i += r - 1;
            if text[i] != 32 {
                count = bpe_chunk(text, i, i + 1, out, count);
                i += 1;
                continue;
            }
            start = i;
            i += 1;
        }
        // ahora text[i] es el primer carácter no-espacio del chunk (start puede incluir ' ')
        if i >= n { count = bpe_chunk(text, start, n, out, count); break; }
        let d = text[i];
        if is_letter(d) {
            while i < n && is_letter(text[i]) { i += 1; }
        } else if is_digit(d) {
            i += 1;
        } else {
            while i < n && !is_space(text[i]) && !is_letter(text[i]) && !is_digit(text[i]) { i += 1; }
        }
        count = bpe_chunk(text, start, i, out, count);
    }
    return count;
}

// Bytes originales del token `id`. Devuelve la cantidad escrita en out.
fn tokenizer_decode(id: u64, out: []u8) -> u64 {
    if id >= n_tokens { return 0; }
    var n: u64 = 0;
    let len = tok_len[id] as u64;
    unsafe {
        let p = tok_off[id] as ptr<u8>;
        var i: u64 = 0;
        while i < len && n < out.len {
            let c = p[i] as u32;
            var cp: u32 = c;
            if c >= 0xF0 { cp = ((c & 0x07) << 18) | (((p[i + 1] as u32) & 0x3F) << 12) | (((p[i + 2] as u32) & 0x3F) << 6) | ((p[i + 3] as u32) & 0x3F); i += 4; }
            else if c >= 0xE0 { cp = ((c & 0x0F) << 12) | (((p[i + 1] as u32) & 0x3F) << 6) | ((p[i + 2] as u32) & 0x3F); i += 3; }
            else if c >= 0xC0 { cp = ((c & 0x1F) << 6) | ((p[i + 1] as u32) & 0x3F); i += 2; }
            else { i += 1; }
            if cp < 512 { out[n] = cp_to_byte[cp as u64]; } else { out[n] = 63; }
            n += 1;
        }
    }
    return n;
}

// UTF-8 is classified by codepoint, while byte-level BPE receives exact bytes.
fn utf_width(text: []u8, i: u64) -> u64 {
    if i >= text.len { return 0; }
    let c = text[i]; var width: u64 = 1;
    if c >= 0xC2 && c <= 0xDF { width = 2; }
    if c >= 0xE0 && c <= 0xEF { width = 3; }
    if c >= 0xF0 && c <= 0xF4 { width = 4; }
    if i + width > text.len { return 1; }
    for j in 1..width { if (text[i + j] & 0xC0) != 0x80 { return 1; } }
    return width;
}
fn utf_cp(text: []u8, i: u64) -> u32 {
    let w = utf_width(text, i); let c = text[i] as u32;
    if w == 1 { return c; }
    var cp: u32 = c & 31;
    if w == 3 { cp = c & 15; }
    if w == 4 { cp = c & 7; }
    for j in 1..w { cp = (cp << 6) | ((text[i + j] as u32) & 63); }
    return cp;
}
fn letter_mark(cls: u64) -> bool {
    if pre_type == 3 { return (cls & 3) != 0; }
    return (cls & 1) != 0;
}
// Exact ordered alternatives from Qwen tokenizer.json (Qwen3.5 adds Marks).
fn encode_qwen(text: []u8, out: []u32) -> u64 {
    var i: u64 = 0; var count: u64 = 0;
    while i < text.len {
        let start = i; let cp = utf_cp(text, i); let cls = unicode_class(cp);
        let width = utf_width(text, i); let contraction = contraction_len(text, i);
        if contraction != 0 { i += contraction; count = bpe_chunk(text, start, i, out, count); continue; }
        var word = i;
        // Optional non-CR/LF/Letter/Number prefix, including one whitespace.
        if cp != 13 && cp != 10 && (cls & 5) == 0 && i + width < text.len {
            if letter_mark(unicode_class(utf_cp(text, i + width))) { word += width; }
        }
        if letter_mark(unicode_class(utf_cp(text, word))) {
            i = word;
            while i < text.len && letter_mark(unicode_class(utf_cp(text, i))) { i += utf_width(text, i); }
            count = bpe_chunk(text, start, i, out, count); continue;
        }
        if (cls & 4) != 0 { i += width; count = bpe_chunk(text, start, i, out, count); continue; }
        var punct = i;
        if cp == 32 && i + 1 < text.len { punct += 1; }
        let pc = unicode_class(utf_cp(text, punct));
        if (pc & 12) == 0 && !letter_mark(pc) {
            i = punct;
            while i < text.len {
                let cl = unicode_class(utf_cp(text, i));
                if (cl & 12) != 0 || letter_mark(cl) { break; }
                i += utf_width(text, i);
            }
            while i < text.len && (text[i] == 13 || text[i] == 10) { i += 1; }
            count = bpe_chunk(text, start, i, out, count); continue;
        }
        if (cls & 8) != 0 {
            var end = i; var last_nl = i; var previous = i;
            while end < text.len && (unicode_class(utf_cp(text, end)) & 8) != 0 {
                previous = end; let c = utf_cp(text, end); end += utf_width(text, end);
                if c == 13 || c == 10 { last_nl = end; }
            }
            if last_nl > i { i = last_nl; }
            else if end == text.len || previous == i { i = end; }
            else { i = previous; }
            count = bpe_chunk(text, start, i, out, count); continue;
        }
        // Invalid UTF-8 still round-trips as bytes; no loop without progress.
        i += width; count = bpe_chunk(text, start, i, out, count);
    }
    return count;
}
fn tokenizer_encode(text: []u8, out: []u32) -> u64 {
    if text.len > 4096 { platform_log("tokenizer: entrada excede 4096 bytes"); return 0; }
    if pre_type == 1 { return encode_smollm(text, out); }
    var start: u64 = 0; var i: u64 = 0; var count: u64 = 0;
    while i < text.len {
        var best = NO_TOKEN; var width: u64 = 0;
        for j in 0..added_n {
            let id = added_ids[j] as u64; let n = tok_len[id] as u64;
            if n <= width || n > text.len - i { continue; }
            var eq = true;
            unsafe { let p = tok_off[id] as ptr<u8>; for k in 0..n { if p[k] != text[i + k] { eq = false; } } }
            if eq { best = id; width = n; }
        }
        if best == NO_TOKEN { i += 1; continue; }
        if count < out.len { count += encode_qwen(text[start..i], out[count..out.len]); }
        if count < out.len { out[count] = best as u32; count += 1; }
        i += width; start = i;
    }
    if count < out.len { count += encode_qwen(text[start..text.len], out[count..out.len]); }
    return count;
}
