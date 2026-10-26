// D0 trusted page surface. L2 supplies bounded positioned text; only this
// renderer chooses pixels. The header/halo is outside the writable page area.
import fb;
import docfont;
import font;
import console;
var doc_pixels: [32768]u8;
var sheet_x: i64 = 0; var sheet_y: i64 = 88; var sheet_w: i64 = 0; var sheet_h: i64 = 0;
var page_w: u64 = 0; var page_h: u64 = 0;
var df: ptr<Fb>;

fn dv_get(b: []u8, at: u64) -> u64 {
    var v: u64 = 0; for i in 0..8 { v = v | ((b[at + i] as u64) << (8 * i)); } return v;
}
fn dv_decode(b: []u8, at: u64) -> u64 {
    let c = b[at] as u64;
    if c < 128 { return (1 << 32) | c; }
    var n: u64 = 0; var v: u64 = 0; var minimum: u64 = 0;
    if c >= 194 && c <= 223 { n = 2; v = c & 31; minimum = 128; }
    if c >= 224 && c <= 239 { n = 3; v = c & 15; minimum = 2048; }
    if c >= 240 && c <= 244 { n = 4; v = c & 7; minimum = 65536; }
    if n == 0 || at + n > b.len { return 0; }
    for i in 1..n { let t = b[at + i] as u64; if t < 128 || t > 191 { return 0; } v = (v << 6) | (t & 63); }
    if v < minimum || v > 1114111 || (v >= 55296 && v <= 57343) { return 0; }
    return (n << 32) | v;
}

fn dv_valid(b: []u8) -> bool {
    if b.len < 128 || dv_get(b, 0) != 0x31434f44 { return false; }
    let w = dv_get(b, 16); let h = dv_get(b, 24);
    if w < 100000 || w > 420000 || h < 100000 || h > 420000 { return false; }
    for i in 4..8 { if dv_get(b, i * 8) > 150000 { return false; } }
    if dv_get(b, 48) + dv_get(b, 56) >= w || dv_get(b, 32) + dv_get(b, 40) >= h { return false; }
    if dv_get(b, 64) > 1 || dv_get(b, 72) > 1 || dv_get(b, 80) < 80 || dv_get(b, 80) > 240 { return false; }
    if dv_get(b, 88) == 0 || dv_get(b, 88) > dv_get(b, 96) || dv_get(b, 96) > 32 { return false; }
    if dv_get(b, 112) > w + 1 || dv_get(b, 120) > h { return false; }
    let count = dv_get(b, 104); if count > 128 { return false; }
    var p: u64 = 128;
    for i in 0..count {
        if p + 40 > b.len { return false; }
        if dv_get(b, p) >= w || dv_get(b, p + 8) >= h { return false; }
        if dv_get(b, p + 16) < 80 || dv_get(b, p + 16) > 240 || dv_get(b, p + 24) > 1 { return false; }
        let n = dv_get(b, p + 32); if n > 224 || p + 40 + n > b.len { return false; }
        let text = b[p + 40..p + 40 + n]; var q: u64 = 0;
        while q < n { let d = dv_decode(text, q); if d == 0 || (d & 0xffffffff) < 32 { return false; } q += d >> 32; }
        p += 40 + n;
    }
    return p == b.len;
}

fn dv_label(x: i64, y: i64, s: str, color: u32) {
    if x >= (df.width as i64) - 260 && y + 16 > (df.height as i64) - 136 { return; }
    var n: u64 = 0;
    unsafe {
        while s[n] != 0 {
            for r in 0..16 { let bits = font_row(s[n], r);
                for c in 0..8 { if (bits & ((0x80 as u8) >> (c as u8))) != 0 { fb_pixel(df, x + (n as i64) * 8 + (c as i64), y + (r as i64), color); } }
            }
            n += 1;
        }
    }
}
fn dv_points(x: i64, y: i64, v: u64, color: u32) {
    var t: [24]u8; var n: u64 = 0;
    if v >= 100 { t[n] = (48 + v / 100) as u8; n += 1; }
    t[n] = (48 + (v / 10) % 10) as u8; n += 1;
    if v % 10 != 0 { t[n] = 46; t[n + 1] = (48 + v % 10) as u8; n += 2; }
    t[n] = 32; t[n + 1] = 112; t[n + 2] = 116; t[n + 3] = 0;
    unsafe { dv_label(x, y, &t[0] as str, color); }
}
fn dv_number(x: i64, y: i64, v: u64, color: u32, unit: str) {
    var text: [64]u8; var digits: [20]u8; var n: u64 = 0; var a = v;
    loop { digits[n] = (48 + a % 10) as u8; n += 1; a = a / 10; if a == 0 { break; } }
    var p: u64 = 0;
    while n > 0 { n -= 1; text[p] = digits[n]; p += 1; }
    unsafe { var j: u64 = 0; while unit[j] != 0 { text[p] = unit[j]; p += 1; j += 1; } text[p] = 0; dv_label(x, y, &text[0] as str, color); }
}
fn dv_cm(x: i64, y: i64, v: u64, color: u32) {
    var t: [32]u8; let whole = v / 10000;
    var n: u64 = 0; if whole >= 10 { t[n] = (48 + whole / 10) as u8; n += 1; }
    t[n] = (48 + whole % 10) as u8; t[n + 1] = 46;
    t[n + 2] = (48 + (v / 1000) % 10) as u8; t[n + 3] = (48 + (v / 100) % 10) as u8;
    t[n + 4] = 32; t[n + 5] = 99; t[n + 6] = 109; t[n + 7] = 0;
    unsafe { dv_label(x, y, &t[0] as str, color); }
}

fn dv_glyph(x: u64, y: u64, font: u64, size: u64, ch: u64) {
    let em = size * 25400 * (sheet_h as u64) * 1024 / (720 * page_h);
    let width = (40 * em + 32767) / 32768; let height = (48 * em + 32767) / 32768;
    let left = sheet_x + ((x * (sheet_w as u64) / page_w) as i64) - ((3 * em / 32768) as i64);
    let top = sheet_y + ((y * (sheet_h as u64) / page_h) as i64) - ((36 * em / 32768) as i64);
    for py in 0..height {
        for px in 0..width {
            let sx0 = px * 32768 / em; let sy0 = py * 32768 / em;
            var sx1 = ((px + 1) * 32768 + em - 1) / em; var sy1 = ((py + 1) * 32768 + em - 1) / em;
            if sx1 > 40 { sx1 = 40; } if sy1 > 48 { sy1 = 48; }
            var alpha: u64 = 0; var samples: u64 = 0;
            for sy in sy0..sy1 { for sx in sx0..sx1 { alpha += docfont_alpha(font, ch, sx, sy); samples += 1; } }
            let dx = left + (px as i64); let dy = top + (py as i64);
            if samples > 0 && alpha > 0 && dx >= sheet_x && dy >= sheet_y && dx < sheet_x + sheet_w && dy < sheet_y + sheet_h {
                let shade = (255 - (alpha / samples) * 230 / 255) as u8;
                fb_pixel(df, dx, dy, fb_rgb(df, shade, shade, shade));
            }
        }
    }
}

fn document_view(p: ptr<u8>, len: u64) -> bool {
    df = console_framebuffer();
    if len == 0 { console_document_surface(false); return true; }
    if len > doc_pixels.len || len < 128 { return false; }
    unsafe { for i in 0..len { doc_pixels[i] = p[i]; } }
    let b = doc_pixels[0..len];
    if !dv_valid(b) { return false; }
    // Validate all bytes before touching any surface.
    if df.width < 640 || df.height < 480 { return false; }
    page_w = dv_get(b, 16); page_h = dv_get(b, 24);
    sheet_h = (df.height as i64) - 224;
    sheet_w = ((sheet_h as u64) * page_w / page_h) as i64;
    let maxw = (df.width as i64) - 360;
    if sheet_w > maxw { sheet_w = maxw; sheet_h = ((sheet_w as u64) * page_h / page_w) as i64; }
    sheet_x = ((df.width as i64) - 280 - sheet_w) / 2;
    console_document_surface(true);
    let white = fb_rgb(df, 255, 255, 255);
    fb_rect(df, sheet_x + 6, sheet_y + 7, sheet_w, sheet_h, fb_rgb(df, 22, 20, 22));
    fb_rect(df, sheet_x, sheet_y, sheet_w, sheet_h, white);
    let label = fb_rgb(df, 181, 167, 155); let fg = fb_rgb(df, 235, 223, 210);
    let right = (df.width as i64) - 260;
    dv_label(right, 104, "DOCUMENTO", fg);
    dv_number(right, 136, dv_get(b, 88), label, " / pagina");
    dv_number(right, 156, dv_get(b, 96), label, " paginas en total");
    dv_number(right, 188, page_w / 1000, fg, " mm de ancho");
    dv_number(right, 208, page_h / 1000, fg, " mm de alto");
    dv_label(right, 244, "Arriba / abajo", label);
    dv_cm(right, 266, dv_get(b, 32), fg); dv_cm(right + 112, 266, dv_get(b, 40), fg);
    if dv_get(b, 64) == 1 { dv_label(right, 302, "Interior / exterior", label); }
    else { dv_label(right, 302, "Izquierda / derecha", label); }
    dv_cm(right, 324, dv_get(b, 48), fg); dv_cm(right + 112, 324, dv_get(b, 56), fg);
    if dv_get(b, 64) == 1 { dv_label(right, 360, "Margenes espejo", fg); }
    else { dv_label(right, 360, "Margenes normales", fg); }
    if dv_get(b, 72) == 0 { dv_label(right, 402, "DejaVu Serif", fg); }
    else { dv_label(right, 402, "DejaVu Sans", fg); }
    if df.height >= 640 { dv_points(right, 424, dv_get(b, 80), label); }
    dv_number(right, 464, dv_get(b, 8), label, " revision");
    var at: u64 = 128;
    for i in 0..dv_get(b, 104) {
        var x = dv_get(b, at); let y = dv_get(b, at + 8); let size = dv_get(b, at + 16); let font = dv_get(b, at + 24); let n = dv_get(b, at + 32);
        let text = b[at + 40..at + 40 + n]; var q: u64 = 0;
        while q < n {
            let ch = dv_decode(text, q); dv_glyph(x, y, font, size, ch & 0xffffffff);
            x += docfont_advance(font, ch & 0xffffffff) * size * 25400 / 1474560; q += ch >> 32;
        }
        at += 40 + n;
    }
    if dv_get(b, 112) > 0 {
        let x = sheet_x + (((dv_get(b, 112) - 1) * (sheet_w as u64) / page_w) as i64);
        let y = sheet_y + ((dv_get(b, 120) * (sheet_h as u64) / page_h) as i64);
        let h = (dv_get(b, 80) * 25400 * (sheet_h as u64) / (720 * page_h)) as i64;
        if x < sheet_x + sheet_w && y - h >= sheet_y && y + 2 <= sheet_y + sheet_h {
            fb_rect(df, x, y - h, 1, h + 2, fb_rgb(df, 50, 45, 40));
        }
        dv_label(24, (df.height as i64) - 128, "EDITANDO  |  Enter: parrafo  |  Esc: volver a las instrucciones", fg);
    } else { dv_label(24, (df.height as i64) - 128, "/doc editar  |  /doc ia <peticion>  |  /doc ayuda", fg); }
    return true;
}
