// D0 document engine, owned by the isolated L2 system broker. All units are
// micrometres; font size is tenths of a point. No model output is executable.
extern c "ai_wire.h" { fn ai_get64(b: []u8, off: u64) -> u64; fn ai_put64(b: []u8, off: u64, v: u64); }
extern c "docfont.h" { fn docfont_advance(font: u64, ch: u64) -> u64; }
extern c "usys.h" { fn usys(nr: u64, a: u64, b: u64, c: u64) -> u64; }
extern c "ulib.h" { fn ulog(s: str) -> u64; fn ulog_bytes(b: []u8) -> u64; fn ulog_num(s: str, n: u64); }

const DOC_MAGIC: u64 = 0x31434f44;
const STATE_MAGIC: u64 = 0x31444c45;
const TEXT_AT: u64 = 224;
const TEXT_MAX: u64 = 16384;
var document_state: [16608]u8;
var document_work: [16608]u8;
var document_undo: [66432]u8;
var undo_count: u64 = 0;
var document_last_id: u64 = 0;
var document_receipt: u64 = 0;
var document_identity_value: u64 = 0;
var document_visible: bool = false;
var document_reply: [256]u8;
var upload: [16384]u8;
var upload_n: u64 = 0;
var upload_revision: u64 = 0;
var upload_active: bool = false;
var view_packet: [32768]u8;

struct DocLine { offset: u64; length: u64; page: u64; x: u64; y: u64; size: u64; }
var lines: [1024]DocLine;
var line_count: u64 = 0;
var page_count: u64 = 1;
var caret_x: u64 = 0;
var caret_y: u64 = 0;
var caret_page: u64 = 1;

fn doc_zero(b: []u8) { for i in 0..b.len { b[i] = 0; } }
fn doc_copy(dst: []u8, src: []u8) { for i in 0..src.len { dst[i] = src[i]; } }
fn doc_set_text(b: []u8, at: u64, s: str) -> u64 {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { b[at + n] = s[n]; n += 1; } }
    return n;
}

fn document_init() {
    document_last_id = 0; document_receipt = 0;
    doc_zero(document_state[..]);
    ai_put64(document_state[..], 0, STATE_MAGIC);
    ai_put64(document_state[..], 8, 1);
    ai_put64(document_state[..], 16, 210000); ai_put64(document_state[..], 24, 297000);
    for i in 4..8 { ai_put64(document_state[..], i * 8, 25000); }
    ai_put64(document_state[..], 80, 110);
    ai_put64(document_state[..], 104, 1);
    ai_put64(document_state[..], 112, doc_set_text(document_state[..], 128, "Documento sin titulo"));
    undo_count = 0; upload_active = false;
    doc_layout(document_state[..]);
}

// Packed scalar + byte length; rejects malformed UTF-8 rather than splitting it.
fn doc_decode(b: []u8, at: u64) -> u64 {
    if at >= b.len { return 0; }
    let c = b[at] as u64;
    if c < 128 { return (1 << 32) | c; }
    var n: u64 = 0; var v: u64 = 0; var minimum: u64 = 0;
    if c >= 194 && c <= 223 { n = 2; v = c & 31; minimum = 128; }
    if c >= 224 && c <= 239 { n = 3; v = c & 15; minimum = 2048; }
    if c >= 240 && c <= 244 { n = 4; v = c & 7; minimum = 65536; }
    if n == 0 || at + n > b.len { return 0; }
    for i in 1..n {
        let t = b[at + i] as u64;
        if t < 128 || t > 191 { return 0; }
        v = (v << 6) | (t & 63);
    }
    if v < minimum || v > 1114111 || (v >= 55296 && v <= 57343) { return 0; }
    return (n << 32) | v;
}

fn doc_text_valid(b: []u8, title: bool) -> bool {
    var p: u64 = 0;
    while p < b.len {
        let d = doc_decode(b, p);
        if d == 0 { return false; }
        let c = d & 0xffffffff;
        if c < 32 && (title || (c != 10 && c != 12)) { return false; }
        if c >= 127 && c < 160 { return false; }
        p += d >> 32;
    }
    return true;
}

fn doc_advance(font: u64, size: u64, ch: u64) -> u64 {
    return docfont_advance(font, ch) * size * 25400 / 1474560;
}
fn doc_left(b: []u8, page: u64) -> u64 {
    if ai_get64(b, 64) == 1 && page % 2 == 0 { return ai_get64(b, 56); }
    return ai_get64(b, 48);
}

fn doc_style_valid(b: []u8) -> bool {
    let w = ai_get64(b, 16); let h = ai_get64(b, 24);
    if w < 100000 || w > 420000 || h < 100000 || h > 420000 { return false; }
    for i in 4..8 { if ai_get64(b, i * 8) > 150000 { return false; } }
    if ai_get64(b, 48) + ai_get64(b, 56) + 30000 > w { return false; }
    if ai_get64(b, 32) + ai_get64(b, 40) + 40000 > h { return false; }
    return ai_get64(b, 64) <= 1 && ai_get64(b, 72) <= 1 && ai_get64(b, 80) >= 80 && ai_get64(b, 80) <= 240 && ai_get64(b, 80) % 5 == 0;
}

fn doc_layout(b: []u8) -> bool {
    line_count = 0; page_count = 1;
    let font = ai_get64(b, 72);
    let width = ai_get64(b, 16) - ai_get64(b, 48) - ai_get64(b, 56);
    let bottom = ai_get64(b, 24) - ai_get64(b, 40);
    let top = ai_get64(b, 32);
    let body_size = ai_get64(b, 80);
    var y = top;
    var page: u64 = 1;
    caret_x = doc_left(b, 1); caret_y = top; caret_page = 1;
    for section in 0..2 {
        var base: u64 = TEXT_AT; var length = ai_get64(b, 88); var size = body_size;
        if section == 0 { base = 128; length = ai_get64(b, 112); size = 180; }
        let height = size * 25400 * 135 / 72000;
        let ascent = size * 25400 / 720;
        let text = b[base..base + length];
        var start: u64 = 0;
        loop {
            if y + height > bottom { page += 1; y = top; }
            if page > 32 || line_count >= 1024 { return false; }
            if start < length && text[start] == 12 { page += 1; y = top; start += 1; continue; }
            var p = start; var used: u64 = 0; var space: u64 = 0;
            while p < length {
                let d = doc_decode(text, p); if d == 0 { return false; }
                let ch = d & 0xffffffff;
                if ch == 10 || ch == 12 { break; }
                let next = p + (d >> 32);
                let step = doc_advance(font, size, ch);
                if (used + step > width || next - start > 224) && p > start { break; }
                used += step; p = next;
                if ch == 32 { space = p; }
            }
            var end = p;
            if p < length && text[p] != 10 && text[p] != 12 && space > start { end = space; }
            lines[line_count].offset = base + start;
            lines[line_count].length = end - start;
            lines[line_count].page = page;
            lines[line_count].x = doc_left(b, page);
            lines[line_count].y = y + ascent;
            lines[line_count].size = size;
            line_count += 1;
            if section == 1 {
                let cursor = ai_get64(b, 96);
                if cursor >= start && cursor <= end {
                    var x = doc_left(b, page); var q = start;
                    while q < cursor { let d = doc_decode(text, q); x += doc_advance(font, size, d & 0xffffffff); q += d >> 32; }
                    caret_x = x; caret_y = y + ascent; caret_page = page;
                }
            }
            y += height;
            start = end;
            if start >= length { break; }
            if text[start] == 10 { start += 1; }
            else { while start < length && text[start] == 32 { start += 1; } }
        }
        if section == 0 { y += 5000; }
    }
    page_count = page;
    return true;
}

fn doc_save_undo() {
    if undo_count == 4 {
        for i in 0..49824 { document_undo[i] = document_undo[i + 16608]; }
        undo_count = 3;
    }
    let start = undo_count * 16608;
    doc_copy(document_undo[start..start + 16608], document_state[..]);
    undo_count += 1;
}

fn doc_present(close: bool, b: []u8) -> bool {
    if close { let ok=usys(18,3,0,0)==0; if ok { document_visible=false; } return ok; }
    doc_zero(view_packet[..]);
    var page = ai_get64(b, 104);
    if page < 1 { page = 1; }
    if page > page_count { page = page_count; }
    ai_put64(b, 104, page);
    ai_put64(view_packet[..], 0, DOC_MAGIC);
    ai_put64(view_packet[..], 8, ai_get64(b, 8));
    for i in 2..11 { ai_put64(view_packet[..], i * 8, ai_get64(b, i * 8)); }
    ai_put64(view_packet[..], 88, page);
    ai_put64(view_packet[..], 96, page_count);
    if ai_get64(b, 120) == 1 && caret_page == page {
        ai_put64(view_packet[..], 112, caret_x + 1); ai_put64(view_packet[..], 120, caret_y);
    }
    var n: u64 = 128; var count: u64 = 0;
    for i in 0..line_count {
        let l = &lines[i];
        if l.page == page {
            if n + 40 + l.length > view_packet.len { return false; }
            ai_put64(view_packet[..], n, l.x); ai_put64(view_packet[..], n + 8, l.y);
            ai_put64(view_packet[..], n + 16, l.size); ai_put64(view_packet[..], n + 24, ai_get64(b, 72));
            ai_put64(view_packet[..], n + 32, l.length);
            doc_copy(view_packet[n + 40..n + 40 + l.length], b[l.offset..l.offset + l.length]);
            n += 40 + l.length; count += 1;
        }
    }
    ai_put64(view_packet[..], 104, count);
    unsafe { let ok=usys(18,3,&view_packet[0] as u64,n)==0; if ok { document_visible=true; } return ok; }
}

// Export transports an exact, versioned snapshot over the existing serial sink.
// The host decoder requires complete offsets/length and rejects truncated frames.
fn doc_export() {
    ulog("doc: export begin ELD1");
    let n = TEXT_AT + ai_get64(document_state[..], 88);
    var hex: [128]u8; var p: u64 = 0; var hash: u64 = 14695981039346656037;
    while p < n {
        var used: u64 = 0;
        while p < n && used < hex.len {
            let byte = document_state[p];
            hash = (hash ^ (byte as u64)) * 1099511628211;
            let hi = byte >> 4; let lo = byte & 15;
            hex[used] = 48 + hi; if hi > 9 { hex[used] = 87 + hi; }
            hex[used + 1] = 48 + lo; if lo > 9 { hex[used + 1] = 87 + lo; }
            used += 2; p += 1;
        }
        ulog_bytes(hex[0..used]);
    }
    ulog_num("doc: export hash=", hash);
    ulog("doc: export end ELD1");
}

fn doc_apply(req: []u8) -> u64 {
    let op = ai_get64(req, 16); let a = ai_get64(req, 32); let c = ai_get64(req, 40);
    let d = ai_get64(req, 48); let e = ai_get64(req, 56); let payload = req[72..];
    let b = document_work[..]; let n = ai_get64(b, 88);
    if op == 1 {
        ai_put64(b, 16, 210000); ai_put64(b, 24, 297000);
        for i in 4..8 { ai_put64(b, i * 8, 25000); }
        ai_put64(b, 64, 0); ai_put64(b, 72, 0); ai_put64(b, 80, 110);
        for i in 128..b.len { b[i] = 0; }
        ai_put64(b, 88, 0); ai_put64(b, 96, 0); ai_put64(b, 104, 1); ai_put64(b, 120, 0);
        ai_put64(b, 112, doc_set_text(b, 128, "Documento sin titulo"));
    } else if op == 2 {
        if payload.len == 0 || payload.len > 96 || !doc_text_valid(payload, true) { return 5; }
        for i in 128..224 { b[i] = 0; } doc_copy(b[128..224], payload); ai_put64(b, 112, payload.len);
    } else if op == 3 {
        if payload.len == 0 || !doc_text_valid(payload, false) { return 5; }
        var extra: u64 = 0; if n > 0 { extra = 1; }
        if n + extra + payload.len > TEXT_MAX { return 6; }
        if extra > 0 { b[TEXT_AT + n] = 10; }
        doc_copy(b[TEXT_AT + n + extra..], payload);
        ai_put64(b, 88, n + extra + payload.len); ai_put64(b, 96, n + extra + payload.len);
    } else if op == 4 {
        ai_put64(b, 32, a); ai_put64(b, 40, c); ai_put64(b, 48, d); ai_put64(b, 56, e);
    } else if op == 5 {
        ai_put64(b, 16, a); ai_put64(b, 24, c);
    } else if op == 6 {
        ai_put64(b, 72, a); ai_put64(b, 80, c);
    } else if op == 7 { ai_put64(b, 64, a);
    } else if op == 9 {
        if a == 0 || a > page_count { return 5; } ai_put64(b, 104, a);
    } else if op == 17 {
        if payload.len == 0 || !doc_text_valid(payload, false) || n + payload.len > TEXT_MAX { return 5; }
        let cursor = ai_get64(b, 96);
        var i = n;
        while i > cursor { i -= 1; b[TEXT_AT + i + payload.len] = b[TEXT_AT + i]; }
        doc_copy(b[TEXT_AT + cursor..], payload);
        ai_put64(b, 88, n + payload.len); ai_put64(b, 96, cursor + payload.len);
    } else if op == 18 {
        let cursor = ai_get64(b, 96);
        if cursor == 0 { return 0; }
        var start = cursor - 1;
        while start > 0 && (b[TEXT_AT + start] & 192) == 128 { start -= 1; }
        for i in cursor..n { b[TEXT_AT + start + i - cursor] = b[TEXT_AT + i]; }
        ai_put64(b, 88, n - (cursor - start)); ai_put64(b, 96, start);
    } else if op == 19 {
        if a > 3 { return 5; }
        var cursor = ai_get64(b, 96);
        if a == 0 && cursor > 0 { cursor -= 1; while cursor > 0 && (b[TEXT_AT + cursor] & 192) == 128 { cursor -= 1; } }
        if a == 1 && cursor < n { cursor += doc_decode(b[TEXT_AT..TEXT_AT + n], cursor) >> 32; }
        if a == 2 { cursor = 0; } if a == 3 { cursor = n; }
        ai_put64(b, 96, cursor);
    } else if op == 20 { if a > 1 { return 5; } ai_put64(b, 120, a);
    } else { return 4; }
    if !doc_style_valid(b) { return 5; }
    return 0;
}

fn document_handle(req: []u8) -> []u8 {
    var status: u64 = 0; var id: u64 = 0; var op: u64 = 0;
    if req.len >= 24 { id = ai_get64(req, 8); op = ai_get64(req, 16); }
    document_receipt += 1;
    if req.len < 72 || req.len > 256 { status = 1; }
    else if ai_get64(req, 0) != DOC_MAGIC || ai_get64(req, 64) != req.len - 72 { status = 2; }
    else if id == 0 || id <= document_last_id { status = 3; }
    else if op > 21 { status = 4; }
    else {
        document_last_id = id;
        let revision = ai_get64(document_state[..], 8);
        if op != 0 && op != 16 && op != 21 && ai_get64(req, 24) != revision { status = 7; }
        else if op == 0 || op == 16 || op == 21 { }
        else if op == 10 { if !doc_present(true, document_state[..]) { status = 9; } }
        else if op == 11 { doc_export(); }
        else if op == 12 { upload_active = true; upload_n = 0; upload_revision = revision; }
        else if op == 13 {
            if !upload_active || upload_revision != revision || ai_get64(req, 32) != upload_n { status = 7; }
            else if upload_n + req.len - 72 > TEXT_MAX { status = 6; }
            else { doc_copy(upload[upload_n..], req[72..]); upload_n += req.len - 72; }
        } else if op == 15 { upload_active = false; upload_n = 0; }
        else {
            doc_copy(document_work[..], document_state[..]);
            var mutate = op != 9 && op != 19 && op != 20;
            if op == 8 {
                if undo_count == 0 { status = 8; }
                else { let at = (undo_count - 1) * 16608; doc_copy(document_work[..], document_undo[at..at + 16608]); }
            } else if op == 14 {
                if !upload_active || upload_revision != revision { status = 7; }
                else if !doc_text_valid(upload[0..upload_n], false) { status = 5; }
                else {
                    doc_copy(document_work[TEXT_AT..], upload[0..upload_n]);
                    ai_put64(document_work[..], 88, upload_n); ai_put64(document_work[..], 96, upload_n);
                }
                upload_active = false;
            } else { status = doc_apply(req); }
            if status == 0 && !doc_layout(document_work[..]) { status = 6; }
            if status == 0 {
                let acting = mutate && op != 17 && op != 18;
                if acting { usys(13, 3, 3, 0); }
                if mutate { ai_put64(document_work[..], 8, revision + 1); }
                if ai_get64(document_work[..], 120) == 1 { ai_put64(document_work[..], 104, caret_page); }
                if !doc_present(false, document_work[..]) { status = 9; doc_layout(document_state[..]); }
                else {
                    if op == 8 { undo_count -= 1; }
                    else if mutate { doc_save_undo(); }
                    doc_copy(document_state[..], document_work[..]);
                    if op == 1 { document_identity_value += 1; }
                }
                if acting { usys(13, 3, 0, 0); }
            } else { doc_layout(document_state[..]); }
        }
    }
    doc_zero(document_reply[..]);
    ai_put64(document_reply[..], 0, DOC_MAGIC); ai_put64(document_reply[..], 8, id);
    ai_put64(document_reply[..], 16, op); ai_put64(document_reply[..], 24, status);
    ai_put64(document_reply[..], 32, document_receipt);
    for i in 1..16 { ai_put64(document_reply[..], 32 + i * 8, ai_get64(document_state[..], i * 8)); }
    ai_put64(document_reply[..], 160, page_count); ai_put64(document_reply[..], 168, undo_count);
    if (op == 16 || op == 21) && status == 0 {
        let at = ai_get64(req, 32); var n = ai_get64(document_state[..], 88); var base = TEXT_AT;
        if op == 21 { n = ai_get64(document_state[..], 112); base = 128; }
        if at > n { ai_put64(document_reply[..], 24, 5); }
        else { var count = n - at; if count > 72 { count = 72; } ai_put64(document_reply[..], 176, count); doc_copy(document_reply[184..], document_state[base + at..base + at + count]); }
    }
    return document_reply[..];
}

// Exact native snapshot; import validates every scalar and UTF-8 boundary before
// presenting or replacing the active document. Identity detaches Save after New.
fn document_identity() -> u64 { return document_identity_value; }
fn document_snapshot() -> []u8 { return document_state[0..224+ai_get64(document_state[..],88)]; }
fn document_redraw() { doc_present(!document_visible,document_state[..]); }
fn document_restore(data: []u8) -> bool {
    if data.len<224 || data.len>16608 || ai_get64(data,0)!=STATE_MAGIC || !doc_style_valid(data) { return false; }
    let n=ai_get64(data,88); let title=ai_get64(data,112); let caret=ai_get64(data,96);
    if n!=data.len-224 || title==0 || title>96 || caret>n || ai_get64(data,104)==0 || ai_get64(data,104)>32 || ai_get64(data,120)>1 { return false; }
    if !doc_text_valid(data[128..128+title],true) || !doc_text_valid(data[224..],false) { return false; }
    if caret<n && (data[224+caret]&192)==128 { return false; }
    doc_zero(document_work[..]); doc_copy(document_work[..],data);
    ai_put64(document_work[..],8,ai_get64(document_state[..],8)+1); ai_put64(document_work[..],120,0);
    if !doc_layout(document_work[..]) || !doc_present(false,document_work[..]) { doc_layout(document_state[..]); return false; }
    doc_copy(document_state[..],document_work[..]); undo_count=0; upload_active=false; document_identity_value+=1; return true;
}
