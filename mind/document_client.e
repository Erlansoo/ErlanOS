// Native document intent adapter. Language selects typed operations, never code.
import ai_wire;
import ulib;
import platform;
import chat;
var dc_id: u64 = 0; var dc_revision: u64 = 0; var dc_receipt: u64 = 0;
var dc_reply: [256]u8;
var dc_edit: bool = false; var dc_open: bool = false;
var dc_norm: [256]u8; var dc_origin: [257]u64; var dc_length: u64 = 0;
var dc_pos: u64 = 0; var dc_value: u64 = 0;

fn dc_write(s: str) { unsafe { platform_write(s as ptr<u8>, ustrlen(s)); } }
fn dc_empty() -> []u8 { return dc_norm[0..0]; }
fn dc_request(op: u64, a: u64, b: u64, c: u64, d: u64, text: []u8) -> bool {
    if text.len > 184 { dc_write("Texto demasiado largo para esta operacion.\n"); return false; }
    var req: [256]u8; for i in 0..req.len { req[i] = 0; }
    dc_id += 1;
    ai_put64(req[..], 0, 0x31434f44); ai_put64(req[..], 8, dc_id); ai_put64(req[..], 16, op); ai_put64(req[..], 24, dc_revision);
    ai_put64(req[..], 32, a); ai_put64(req[..], 40, b); ai_put64(req[..], 48, c); ai_put64(req[..], 56, d); ai_put64(req[..], 64, text.len);
    for i in 0..text.len { req[72 + i] = text[i]; }
    if usend(8, req[0..72 + text.len]) != 0 || urecv(9, dc_reply[..]) != 256 { dc_write("Fallo de comunicacion con Documentos.\n"); return false; }
    if ai_get64(dc_reply[..], 0) != 0x31434f44 || ai_get64(dc_reply[..], 8) != dc_id || ai_get64(dc_reply[..], 16) != op || ai_get64(dc_reply[..], 32) <= dc_receipt {
        dc_write("Respuesta de Documentos invalida.\n"); return false;
    }
    dc_receipt = ai_get64(dc_reply[..], 32); dc_revision = ai_get64(dc_reply[..], 40);
    let status = ai_get64(dc_reply[..], 24);
    var log: [320]u8; var n = fmt_str(log[..], 0, "[doc] request="); n = fmt_u64(log[..], n, dc_id);
    n = fmt_str(log[..], n, " op="); n = fmt_u64(log[..], n, op);
    n = fmt_str(log[..], n, " status="); n = fmt_u64(log[..], n, status);
    n = fmt_str(log[..], n, " revision="); n = fmt_u64(log[..], n, dc_revision);
    n = fmt_str(log[..], n, " pages="); n = fmt_u64(log[..], n, ai_get64(dc_reply[..], 160));
    n = fmt_str(log[..], n, " a="); n = fmt_u64(log[..], n, a); n = fmt_str(log[..], n, " b="); n = fmt_u64(log[..], n, b);
    n = fmt_str(log[..], n, " c="); n = fmt_u64(log[..], n, c); n = fmt_str(log[..], n, " d="); n = fmt_u64(log[..], n, d); ulog_bytes(log[0..n]);
    if status != 0 {
        if status == 7 { dc_write("El documento cambio. Repite la peticion sobre la revision actual.\n"); }
        else if status == 8 { dc_write("No hay otra version para deshacer.\n"); }
        else if status == 6 { dc_write("El documento supera el limite de esta version.\n"); }
        else if status == 9 { dc_write("El estado esta disponible, pero fallo la vista de pagina.\n"); }
        else { dc_write("Configuracion no valida; se conserva el documento anterior.\n"); }
        return false;
    }
    return true;
}
fn dc_simple(op: u64) -> bool { return dc_request(op, 0, 0, 0, 0, dc_empty()); }
fn document_external_opened() { dc_open=true; dc_edit=false; dc_simple(0); }
fn dc_show() -> bool { dc_open = true; return dc_request(9, ai_get64(dc_reply[..], 136), 0, 0, 0, dc_empty()); }
fn dc_confirm() {
    var text: [96]u8; var n = fmt_str(text[..], 0, "Documento actualizado. Revision "); n = fmt_u64(text[..], n, dc_revision);
    n = fmt_str(text[..], n, ".\n"); unsafe { platform_write(&text[0], n); }
}

fn dc_normalize(b: []u8) {
    dc_length = 0; var p: u64 = 0;
    while p < b.len && dc_length < 255 {
        dc_origin[dc_length] = p;
        var ch = b[p]; p += 1;
        if ch == 195 && p < b.len {
            let c = b[p]; p += 1;
            ch = 63;
            if c == 161 || c == 129 { ch = 97; } if c == 169 || c == 137 { ch = 101; }
            if c == 173 || c == 141 { ch = 105; } if c == 179 || c == 147 { ch = 111; }
            if c == 186 || c == 154 || c == 188 || c == 156 { ch = 117; }
            if c == 177 || c == 145 { ch = 110; }
        }
        if ch >= 65 && ch <= 90 { ch += 32; }
        dc_norm[dc_length] = ch; dc_length += 1;
    }
    dc_origin[dc_length] = b.len;
}
fn dc_match(at: u64, s: str) -> bool {
    let n = ustrlen(s); if at + n > dc_length { return false; }
    unsafe { for i in 0..n { if dc_norm[at + i] != s[i] { return false; } } } return true;
}
fn dc_equal(s: str) -> bool { return dc_length == ustrlen(s) && dc_match(0, s); }
fn dc_find(s: str) -> u64 {
    for i in 0..dc_length { if dc_match(i, s) { return i; } } return 256;
}
fn dc_has(s: str) -> bool { return dc_find(s) < 256; }
fn dc_number(at: u64) -> bool {
    dc_pos = at;
    while dc_pos < dc_length && (dc_norm[dc_pos] < 48 || dc_norm[dc_pos] > 57) {
        if dc_norm[dc_pos] == 45 { return false; } dc_pos += 1;
    }
    if dc_pos == dc_length { return false; }
    var whole: u64 = 0;
    while dc_pos < dc_length && dc_norm[dc_pos] >= 48 && dc_norm[dc_pos] <= 57 {
        whole = whole * 10 + ((dc_norm[dc_pos] - 48) as u64); if whole > 10000 { return false; } dc_pos += 1;
    }
    var fraction: u64 = 0; var digits: u64 = 0;
    if dc_pos + 1 < dc_length && (dc_norm[dc_pos] == 46 || dc_norm[dc_pos] == 44) && dc_norm[dc_pos + 1] >= 48 && dc_norm[dc_pos + 1] <= 57 {
        dc_pos += 1;
        while dc_pos < dc_length && dc_norm[dc_pos] >= 48 && dc_norm[dc_pos] <= 57 {
            if digits == 2 { return false; } fraction = fraction * 10 + ((dc_norm[dc_pos] - 48) as u64); digits += 1; dc_pos += 1;
        }
        if digits == 0 { return false; } if digits == 1 { fraction *= 10; }
    }
    dc_value = whole * 100 + fraction; return true;
}
fn dc_measure(at: u64) -> u64 {
    var p = at;
    while p < dc_length && (dc_norm[p] == 32 || dc_norm[p] == 58 || dc_norm[p] == 61) { p += 1; }
    if dc_match(p, "de ") { p += 3; }
    if p >= dc_length || dc_norm[p] < 48 || dc_norm[p] > 57 || !dc_number(p) { return 0xffffffffffffffff; }
    while dc_pos < dc_length && dc_norm[dc_pos] == 32 { dc_pos += 1; }
    var scale: u64 = 100;
    if dc_match(dc_pos, "mm") { dc_pos += 2; scale = 10; }
    else if dc_match(dc_pos, "cm") { dc_pos += 2; }
    while dc_pos < dc_length && dc_norm[dc_pos] == 32 { dc_pos += 1; }
    if dc_pos < dc_length && dc_norm[dc_pos] >= 97 && dc_norm[dc_pos] <= 122 {
        if !dc_match(dc_pos, "abajo") && !dc_match(dc_pos, "arriba") && !dc_match(dc_pos, "izquierda") && !dc_match(dc_pos, "derecha") && !dc_match(dc_pos, "inferior") && !dc_match(dc_pos, "superior") && !dc_match(dc_pos, "interior") && !dc_match(dc_pos, "exterior") && !dc_match(dc_pos, "y ") { return 0xffffffffffffffff; }
    }
    return dc_value * scale; // default cm, also accepts decimal comma
}
fn dc_margin(current: u64, first: str, second: str) -> u64 {
    var at = dc_find(first); var length = ustrlen(first);
    if at == 256 { at = dc_find(second); length = ustrlen(second); }
    if at == 256 { return current; }
    return dc_measure(at + length);
}

fn dc_help() {
    dc_write("Documentos: /doc nuevo | titulo <texto> | texto <parrafo> | editar | ia <peticion>\n");
    dc_write("/doc margenes 2 3 2.5 4 | espejo | normal | papel a4/carta/a5 | fuente serif/sans 11\n");
    dc_write("/doc pagina 2 | salto | deshacer | estado | exportar | cerrar\n");
}
fn dc_draft(prompt: []u8) -> bool {
    dc_write("Redactando el documento... Esc cancela.\n"); platform_clear_cancel();
    let content = model_document_draft(prompt);
    if model_chat_status() > 1 || content.len == 0 { dc_write("Borrador cancelado o no disponible. Documento conservado.\n"); return false; }
    if !dc_simple(12) { return false; }
    var at: u64 = 0;
    while at < content.len {
        var end = at + 184; if end > content.len { end = content.len; }
        if !dc_request(13, at, 0, 0, 0, content[at..end]) { dc_simple(15); return false; }
        at = end;
    }
    let limited = model_chat_status() == 1;
    if !dc_simple(14) { return false; }
    if limited { dc_write("Borrador acotado: alcanzo el limite de generacion de esta version.\n"); }
    return true;
}

fn document_command(line: []u8) -> bool {
    dc_normalize(line);
    let explicit = dc_match(0, "/doc") && (dc_length == 4 || dc_norm[4] == 32);
    if explicit {
        var skip: u64 = 4; while skip < dc_length && dc_norm[skip] == 32 { skip += 1; }
        let original = dc_origin[skip]; dc_normalize(line[original..]);
        // Rebase offsets so title/text retain their original UTF-8 bytes and case.
        for i in 0..=dc_length { dc_origin[i] += original; }
    }
    let creating = dc_equal("nuevo") || dc_equal("crea un documento") || dc_equal("crear un documento") || dc_equal("abre un documento");
    let writing = dc_match(0, "ia ") || ((dc_match(0, "redacta ") || dc_match(0, "haz ") || dc_match(0, "escribe ") || dc_match(0, "crea ")) && (dc_has("documento") || dc_has("informe") || dc_has("carta")) && !creating);
    let styling = dc_has("margen") || dc_has("arriba") || dc_has("abajo") || dc_has("izquierda") || dc_has("derecha") || dc_has("fuente") || dc_has("papel") || dc_has("tamano de pagina");
    let setting = dc_match(0, "pon ") || dc_match(0, "usa ") || dc_match(0, "cambia ") || dc_match(0, "configura ") || dc_match(0, "ajusta ");
    if !explicit && !creating && !writing && !(dc_open && styling && setting) { return false; }
    if !writing && styling {
        if dc_has("pulgada") || dc_has("inch") || dc_has(" no ") { dc_write("Configuracion no admitida. Usa cm o mm y una instruccion afirmativa.\n"); return true; }
        let has_margin_values = dc_has("arriba") || dc_has("abajo") || dc_has("izquierda") || dc_has("derecha") || dc_has("interior") || dc_has("exterior") || (dc_has("margen") && dc_number(0));
        var groups: u64 = 0;
        if has_margin_values { groups += 1; } if dc_has("espejo") { groups += 1; }
        if dc_has("fuente") { groups += 1; } if dc_has("papel") || dc_has("tamano") { groups += 1; }
        if groups > 1 { dc_write("Indica por separado margenes, espejo, fuente y papel. No se aplicaron cambios.\n"); return true; }
    }
    if !dc_simple(0) { return true; }
    var ok = false;
    if creating { ok = dc_simple(1); dc_open = ok; }
    else if writing {
        if !dc_open { if !dc_show() { return true; } }
        var prompt = line;
        if dc_match(0, "ia ") { prompt = line[dc_origin[3]..]; }
        ok = dc_draft(prompt);
    }
    else if dc_equal("") || dc_equal("estado") { ok = dc_show(); }
    else if dc_equal("ayuda") { dc_help(); return true; }
    else if dc_match(0, "titulo ") { ok = dc_request(2, 0, 0, 0, 0, line[dc_origin[7]..]); }
    else if dc_match(0, "texto ") { ok = dc_request(3, 0, 0, 0, 0, line[dc_origin[6]..]); }
    else if dc_equal("salto") { var p: [1]u8; p[0] = 12; ok = dc_request(17, 0, 0, 0, 0, p[..]); }
    else if dc_equal("editar") { ok = dc_request(20, 1, 0, 0, 0, dc_empty()); dc_edit = ok; dc_open = ok; }
    else if dc_equal("deshacer") { ok = dc_simple(8); }
    else if dc_equal("cerrar") { ok = dc_simple(10); if ok { dc_open = false; } }
    else if dc_equal("exportar") { ok = dc_simple(11); if ok { dc_write("Instantanea emitida por serial. El anfitrion puede guardarla y convertirla.\n"); } }
    else if dc_match(0, "pagina ") { if dc_number(7) && dc_value % 100 == 0 { ok = dc_request(9, dc_value / 100, 0, 0, 0, dc_empty()); } }
    else if dc_has("espejo") { ok = dc_request(7, 1, 0, 0, 0, dc_empty()); }
    else if dc_equal("normal") || dc_has("margenes normales") { ok = dc_request(7, 0, 0, 0, 0, dc_empty()); }
    else if dc_has("fuente") {
        var font: u64 = 2; if dc_has("serif") { font = 0; } if dc_has("sans") { font = 1; }
        var size = ai_get64(dc_reply[..], 112);
        if dc_number(0) { if dc_value % 50 != 0 { dc_write("Usa puntos enteros o medios puntos.\n"); return true; } size = dc_value / 10; }
        else { for i in 0..dc_length { if dc_norm[i] >= 48 && dc_norm[i] <= 57 { dc_write("Tamano de fuente invalido.\n"); return true; } } }
        if font < 2 { ok = dc_request(6, font, size, 0, 0, dc_empty()); }
    }
    else if dc_has("papel") || dc_has("tamano") {
        if dc_has("a4") { ok = dc_request(5, 210000, 297000, 0, 0, dc_empty()); }
        else if dc_has("carta") { ok = dc_request(5, 215900, 279400, 0, 0, dc_empty()); }
        else if dc_has("a5") { ok = dc_request(5, 148000, 210000, 0, 0, dc_empty()); }
    }
    else if styling {
        var top = ai_get64(dc_reply[..], 64); var bottom = ai_get64(dc_reply[..], 72);
        var left = ai_get64(dc_reply[..], 80); var right = ai_get64(dc_reply[..], 88);
        let labels = dc_has("arriba") || dc_has("superior") || dc_has("abajo") || dc_has("inferior") || dc_has("izquierda") || dc_has("derecha") || dc_has("interior") || dc_has("exterior");
        if labels {
            top = dc_margin(top, "arriba", "superior"); bottom = dc_margin(bottom, "abajo", "inferior");
            left = dc_margin(left, "izquierda", "interior"); right = dc_margin(right, "derecha", "exterior");
        } else {
            if !dc_number(0) { dc_write("Indica los margenes en cm: arriba, abajo, izquierda y derecha.\n"); return true; }
            var begin: u64 = 0; while begin < dc_length && (dc_norm[begin] < 48 || dc_norm[begin] > 57) { begin += 1; }
            top = dc_measure(begin); bottom = top; left = top; right = top;
            let next = dc_pos;
            if next < dc_length {
                bottom = dc_measure(next);
                left = dc_measure(dc_pos); right = dc_measure(dc_pos);
                if bottom == 0xffffffffffffffff || left == 0xffffffffffffffff || right == 0xffffffffffffffff { dc_write("Indica uno o cuatro margenes.\n"); return true; }
            }
            if dc_pos < dc_length { dc_write("Sobran medidas. Indica uno o cuatro margenes.\n"); return true; }
        }
        ok = dc_request(4, top, bottom, left, right, dc_empty());
    } else { dc_help(); return true; }
    if ok && !dc_edit { dc_confirm(); }
    else if !ok { dc_write("Usa /doc ayuda para ver las configuraciones admitidas.\n"); }
    return true;
}

fn document_editing() -> bool { return dc_edit; }
fn document_key(c: u8) {
    if c == 27 { dc_request(20, 0, 0, 0, 0, dc_empty()); dc_edit = false; return; }
    if c == 8 { dc_simple(18); return; }
    if c >= 128 && c <= 131 { dc_request(19, (c - 128) as u64, 0, 0, 0, dc_empty()); return; }
    if c == 10 || c == 11 || (c >= 32 && c < 127) {
        var text: [1]u8; text[0] = c; if c == 11 { text[0] = 10; }
        dc_request(17, 0, 0, 0, 0, text[..]);
    }
}
