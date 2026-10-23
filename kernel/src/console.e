// L1 — consola de texto sobre el framebuffer (fuente 8x16). Es la superficie mínima de
// salida para servicios. Compositor M4 v0: halo, texto y panel de estado separado.
// Sólo console_present_status dibuja el panel; el texto nunca invade esa región.

extern c "fb.h" {
    struct Fb { base: ptr<u32>; width: u64; height: u64; pitch: u64; rs: u8; gs: u8; bs: u8; }
    fn fb_rgb(f: ptr<Fb>, r: u8, g: u8, b: u8) -> u32;
    fn fb_pixel(f: ptr<Fb>, x: i64, y: i64, color: u32);
    fn fb_rect(f: ptr<Fb>, x: i64, y: i64, w: i64, h: i64, color: u32);
    fn fb_circle(f: ptr<Fb>, cx: i64, cy: i64, r: i64, color: u32);
}
extern c "font.h" { fn font_row(ch: u8, row: u64) -> u8; }
extern c "serial.h" { fn kprintf(fmt: str, ...); }

const GLYPH_W: i64 = 8;
const GLYPH_H: i64 = 16;
const MARGIN: i64 = 24;
const HEADER_H: i64 = 72;    // zona de los puntos / halo
const PANEL_W: i64 = 320;

var fbp: ptr<Fb>;
var ready: bool = false;
var cols: i64 = 0;
var rows: i64 = 0;
var cur_col: i64 = 0;
var cur_row: i64 = 0;
var fg: u32 = 0;
var dim: u32 = 0;
var halo_state: u64 = 0;
var panel_x: i64 = 0;
var panel_width: i64 = 0;
var console_mode: u64 = 0;
var utf8_need: u8 = 0;
var utf8_value: u32 = 0;
var utf8_min: u32 = 0;
var input_anchor: bool = false;
var input_col: i64 = 0;
var input_row: i64 = 0;
var text_top: i64 = HEADER_H;

fn console_framebuffer() -> ptr<Fb> { return fbp; }
fn console_document_surface(active: bool) {
    if !ready { return; }
    canvas_fill(0, HEADER_H, fbp.width as i64, (fbp.height as i64) - HEADER_H);
    text_top = HEADER_H;
    cols = (panel_x - 2 * MARGIN) / GLYPH_W;
    if active { text_top = (fbp.height as i64) - 96; cols = ((fbp.width as i64) - 2 * MARGIN) / GLYPH_W; }
    rows = ((fbp.height as i64) - text_top - MARGIN) / GLYPH_H;
    cur_row = 0; cur_col = 0; input_anchor = false; utf8_need = 0;
}

fn console_init(f: ptr<Fb>) {
    fbp = f;
    fg = fb_rgb(f, 0xd8, 0xd8, 0xde);
    dim = fb_rgb(f, 0x55, 0x55, 0x60);
    panel_width = PANEL_W;
    if (f.width as i64) < PANEL_W * 2 { panel_width = (f.width as i64) / 2; }
    panel_x = (f.width as i64) - panel_width;
    cols = (panel_x - 2 * MARGIN) / GLYPH_W;
    rows = ((f.height as i64) - HEADER_H - MARGIN) / GLYPH_H;
    cur_col = 0;
    cur_row = 0;
    text_top = HEADER_H;
    utf8_need = 0;
    input_anchor = false;
    ready = cols > 0 && rows > 0;
    if !ready { return; }
    canvas_fill(0, 0, f.width as i64, f.height as i64);
    console_set_halo(0);
    kprintf("[console] %dx%d caracteres\n", cols as u64, rows as u64);
    kprintf("[console] geometria header=%d margen=%d panel_x=%d ancho=%d alto=%d\n",
            HEADER_H as u64, MARGIN as u64, panel_x as u64, f.width, f.height);
}

// Lienzo café continuo, también bajo los glifos y la cabecera. Coordenadas
// normalizadas: el resplandor conserva su proporción en cualquier resolución.
// Aritmética entera, sin imagen, heap, FPU ni repintado periódico.
fn canvas_color(x: i64, y: i64) -> u32 {
    let dx = (2 * x + 1 - (fbp.width as i64)) * 1024 / (fbp.width as i64);
    let dy = (2 * y + 1 - (fbp.height as i64)) * 1024 / (fbp.height as i64);
    let falloff = 1024 - (dx * dx + dy * dy) / 2048;
    let squared = falloff * falloff / 1024;
    let glow = squared * squared / 1024;
    // Bayer 4x4 a menos de un nivel por canal para suavizar las bandas de 8 bits.
    let low = ((x ^ y) & 1) * 2 + (y & 1);
    let high = (((x ^ y) >> 1) & 1) * 2 + ((y >> 1) & 1);
    let dither = (low * 4 + high) * 64 + 32;
    let red = 22 + (100 * glow + dither) / 1024;
    let green = 20 + (76 * glow + dither) / 1024;
    let blue = 22 + (54 * glow + dither) / 1024;
    return fb_rgb(fbp, red as u8, green as u8, blue as u8);
}

fn canvas_fill(x: i64, y: i64, w: i64, h: i64) {
    if w <= 0 || h <= 0 { return; }
    var left = x; var top = y;
    var right = x + w; var bottom = y + h;
    if left < 0 { left = 0; }
    if top < 0 { top = 0; }
    if right > (fbp.width as i64) { right = fbp.width as i64; }
    if bottom > (fbp.height as i64) { bottom = fbp.height as i64; }
    for j in top..bottom {
        for i in left..right { fb_pixel(fbp, i, j, canvas_color(i, j)); }
    }
}

fn canvas_disc(cx: i64, cy: i64, radius: i64) {
    for j in -radius..=radius {
        for i in -radius..=radius {
            if i * i + j * j <= radius * radius {
                fb_pixel(fbp, cx + i, cy + j, canvas_color(cx + i, cy + j));
            }
        }
    }
}

// Fixed kernel vocabulary: no caller can supply labels, colors or coordinates.
fn halo_name(state: u64) -> str {
    match state {
        1 => { return "ESCUCHANDO"; }
        2 => { return "RAZONANDO LOCAL"; }
        3 => { return "ACTUANDO"; }
        4 => { return "AUTORIZACION"; }
        5 => { return "NUBE"; }
        _ => { return "IDLE"; }
    }
    return "IDLE";
}

fn header_glyph(x: i64, y: i64, ch: u8, color: u32) {
    if y < 0 || y + GLYPH_H > HEADER_H { return; }
    for r in 0..16 {
        let bits = font_row(ch, r);
        for c in 0..8 {
            var pixel = canvas_color(x + (c as i64), y + (r as i64));
            if (bits & ((0x80 as u8) >> (c as u8))) != 0 { pixel = color; }
            fb_pixel(fbp, x + (c as i64), y + (r as i64), pixel);
        }
    }
}

// Syscall policy reserves 3/4 for PRESENT and 5 for EGRESS. Drawing itself has
// no trace or panel refresh: mode changes and host tests also call this function.
fn console_set_halo(state: u64) {
    if !ready || state > 5 { return; }
    halo_state = state;
    let f = fbp;
    let cx = (f.width as i64) / 2;
    let cy = HEADER_H / 2;
    let r: i64 = 5;
    let gap: i64 = 30;
    canvas_fill(0, 0, f.width as i64, HEADER_H);
    var color = fg;
    if state == 2 {
        color = fb_rgb(f, 0x8a, 0xb4, 0xf8);
        fb_circle(f, cx, cy, 14, dim);
    }
    if state == 3 || state == 4 {
        color = fb_rgb(f, 0xf8, 0xc0, 0x6a);
        fb_rect(f, 0, 0, f.width as i64, 2, color);
        fb_rect(f, 0, HEADER_H - 2, f.width as i64, 2, color);
        fb_rect(f, 0, 0, 2, HEADER_H, color);
        fb_rect(f, (f.width as i64) - 2, 0, 2, HEADER_H, color);
    }
    if state == 4 {
        fb_rect(f, 5, 5, (f.width as i64) - 10, 2, color);
        fb_rect(f, 5, HEADER_H - 7, (f.width as i64) - 10, 2, color);
        fb_rect(f, 5, 5, 2, HEADER_H - 10, color);
        fb_rect(f, (f.width as i64) - 7, 5, 2, HEADER_H - 10, color);
    }
    if state == 5 {
        let cloud = fb_rgb(f, 0xc0, 0x8a, 0xf8);
        let x0 = cx - gap - r - 10;
        let x1 = cx + gap + r + 10;
        let y0 = cy - r - 10;
        let y1 = cy + r + 10;
        fb_rect(f, x0, y0, x1 - x0, 2, cloud);
        fb_rect(f, x0, y1 - 2, x1 - x0, 2, cloud);
        fb_rect(f, x0, y0, 2, y1 - y0, cloud);
        fb_rect(f, x1 - 2, y0, 2, y1 - y0, cloud);
    }
    fb_circle(f, cx - gap, cy, r, color);
    fb_circle(f, cx, cy, r, color);
    fb_circle(f, cx + gap, cy, r, color);
    if state == 1 {
        // escuchando: anillo tenue alrededor del punto central
        fb_circle(f, cx, cy, r + 5, dim);
        canvas_disc(cx, cy, r + 3);
        fb_circle(f, cx, cy, r, color);
    }
    // En reposo sólo quedan los tres puntos. Los estados activos sí se rotulan;
    // el panel verificado conserva siempre su campo Halo, incluido IDLE.
    if state == 0 { return; }
    let name = halo_name(state);
    var n: u64 = 0;
    unsafe {
        while name[n] != 0 {
            header_glyph(cx + 70 + (n as i64) * GLYPH_W, 28, name[n], color);
            n += 1;
        }
    }
}

fn draw_glyph(col: i64, row: i64, ch: u8, color: u32) {
    let f = fbp;
    let x0 = MARGIN + col * GLYPH_W;
    let y0 = text_top + row * GLYPH_H;
    for r in 0..16 {
        let bits = font_row(ch, r);
        for c in 0..8 {
            var px = canvas_color(x0 + (c as i64), y0 + (r as i64));
            if (bits & ((0x80 as u8) >> (c as u8))) != 0 { px = color; }
            fb_pixel(f, x0 + (c as i64), y0 + (r as i64), px);
        }
    }
}

fn scroll() {
    // Compositor v0: se limpia sólo texto; el panel conserva la última instantánea.
    let f = fbp;
    canvas_fill(0, text_top, MARGIN * 2 + cols * GLYPH_W, (f.height as i64) - text_top);
    cur_row = 0;
    cur_col = 0;
    if input_anchor { input_col = 0; input_row = 0; }
}

// Bounded line-editor protocol through Console.Write: SO (14) records an
// input anchor, SI (15) clears from that anchor so L2 can repaint its buffer.
// Only the text region is touched; halo and verified panel remain separate.
fn console_input_mark() {
    input_anchor = true; input_col = cur_col; input_row = cur_row;
}
fn console_input_repaint() {
    if !input_anchor { return; }
    let x = MARGIN + input_col * GLYPH_W;
    let y = text_top + input_row * GLYPH_H;
    canvas_fill(x, y, (cols - input_col) * GLYPH_W, GLYPH_H);
    canvas_fill(MARGIN, y + GLYPH_H, cols * GLYPH_W, (rows - input_row - 1) * GLYPH_H);
    cur_col = input_col; cur_row = input_row;
}

fn console_putc(ch: u8) {
    if !ready { return; }
    if ch == 14 { console_input_mark(); return; }
    if ch == 15 { console_input_repaint(); return; }
    if ch == 10 {
        cur_col = 0;
        cur_row += 1;
        if cur_row >= rows { scroll(); }
        return;
    }
    if ch == 8 {
        if cur_col > 0 { cur_col -= 1; draw_glyph(cur_col, cur_row, 32, fg); }
        return;
    }
    if ch < 32 { return; }
    draw_glyph(cur_col, cur_row, ch, fg);
    cur_col += 1;
    if cur_col >= cols {
        cur_col = 0;
        cur_row += 1;
        if cur_row >= rows { scroll(); }
    }
}

// Los tokens pueden cortar un carácter UTF-8 entre llamadas. Acumularlo evita
// mostrar dos glifos por cada tilde/ñ. La fuente cubre Latin-1; el resto es '?'.
fn console_utf8(ch: u8) {
    if utf8_need > 0 {
        if (ch & 0xc0) == 0x80 {
            utf8_value = (utf8_value << 6) | ((ch & 0x3f) as u32);
            utf8_need -= 1;
            if utf8_need == 0 {
                if utf8_value >= utf8_min && utf8_value >= 160 && utf8_value <= 255 {
                    console_putc(utf8_value as u8);
                } else { console_putc(63); }
            }
            return;
        }
        utf8_need = 0;
        console_putc(63);
    }
    if ch < 128 { console_putc(ch); return; }
    if ch >= 0xc2 && ch <= 0xdf {
        utf8_need = 1; utf8_value = (ch & 0x1f) as u32; utf8_min = 0x80; return;
    }
    if ch >= 0xe0 && ch <= 0xef {
        utf8_need = 2; utf8_value = (ch & 0x0f) as u32; utf8_min = 0x800; return;
    }
    if ch >= 0xf0 && ch <= 0xf4 {
        utf8_need = 3; utf8_value = (ch & 7) as u32; utf8_min = 0x10000; return;
    }
    console_putc(63);
}

fn console_write(p: ptr<u8>, len: u64) {
    unsafe { for i in 0..len { console_utf8(p[i]); } }
}

fn console_ready() -> bool { return ready; }

// El modo aplica una paleta real a la consola; conserva la conversación existente.
// Las nuevas letras y el halo adoptan la paleta; el panel se repinta por system.e.
fn console_set_mode(mode: u64) {
    if mode > 1 || mode == console_mode { return; }
    console_mode = mode;
    if !ready { return; }
    if mode == 1 {
        fg = fb_rgb(fbp, 0xe8, 0xd5, 0xb5);
        dim = fb_rgb(fbp, 0x65, 0x59, 0x49);
    } else {
        fg = fb_rgb(fbp, 0xd8, 0xd8, 0xde);
        dim = fb_rgb(fbp, 0x55, 0x55, 0x60);
    }
    console_set_halo(halo_state);
}

fn panel_glyph(col: u64, row: u64, ch: u8, color: u32, background: u32) {
    let x = panel_x + 20 + (col as i64) * GLYPH_W;
    let y = HEADER_H + 24 + (row as i64) * 20;
    if x + GLYPH_W > (fbp.width as i64) - 12 || y + GLYPH_H > (fbp.height as i64) - 12 { return; }
    for r in 0..16 {
        let bits = font_row(ch, r);
        for c in 0..8 {
            var pixel = background;
            if (bits & ((0x80 as u8) >> (c as u8))) != 0 { pixel = color; }
            fb_pixel(fbp, x + (c as i64), y + (r as i64), pixel);
        }
    }
}

fn panel_text(row: u64, text: str, color: u32, background: u32) {
    var n: u64 = 0;
    unsafe {
        while text[n] != 0 && n < 36 {
            panel_glyph(n, row, text[n], color, background);
            n += 1;
        }
    }
}

fn panel_number(row: u64, value: u64, color: u32, background: u32) {
    var digits: [20]u8;
    var n: u64 = 0;
    var v = value;
    loop {
        digits[n] = (48 + v % 10) as u8;
        n += 1;
        v = v / 10;
        if v == 0 { break; }
    }
    for i in 0..n { panel_glyph(i, row, digits[n - 1 - i], color, background); }
}

fn op_name(op: u64) -> str {
    match op {
        1 => { return "consulta"; }
        2 => { return "modo foco"; }
        3 => { return "modo normal"; }
        4 => { return "panel"; }
        _ => { return "ninguna"; }
    }
    return "ninguna";
}

fn panel_value(row: u64, col: u64, text: str, color: u32, background: u32) {
    var n: u64 = 0;
    unsafe {
        while col + n < 36 && text[n] != 0 {
            panel_glyph(col + n, row, text[n], color, background);
            n += 1;
        }
    }
}

// Contenido estructurado: todos los valores vienen del kernel, ningún puntero de
// usuario, texto libre ni instrucciones de dibujo entran por SYS_PRESENT.
fn console_present_status(mode: u64, revision: u64, uptime: u64, free: u64,
                          total: u64, threads: u64, switches: u64, denied: u64,
                          last_op: u64, last_receipt: u64) {
    if !ready { return; }
    var background = fb_rgb(fbp, 0x12, 0x1c, 0x2a);
    var accent = fb_rgb(fbp, 0x8a, 0xb4, 0xf8);
    if mode == 1 {
        background = fb_rgb(fbp, 0x24, 0x20, 0x18);
        accent = fb_rgb(fbp, 0xe8, 0xc0, 0x80);
    }
    let label = fb_rgb(fbp, 0x9b, 0xa5, 0xb2);
    fb_rect(fbp, panel_x, HEADER_H, panel_width, (fbp.height as i64) - HEADER_H, background);
    fb_rect(fbp, panel_x, HEADER_H, 2, (fbp.height as i64) - HEADER_H, accent);
    panel_text(0, "ESTADO VERIFICADO", accent, background);
    panel_text(1, "Instantanea del kernel", label, background);
    if mode == 1 { panel_text(3, "MODO FOCO", accent, background); }
    else { panel_text(3, "MODO NORMAL", accent, background); }
    panel_text(4, "Revision", label, background);
    panel_number(5, revision, fg, background);
    panel_text(7, "Tiempo activo (ms)", label, background);
    panel_number(8, uptime, fg, background);
    panel_text(10, "Paginas libres (4 KiB)", label, background);
    panel_number(11, free, fg, background);
    panel_text(13, "Limite fisico (paginas)", label, background);
    panel_number(14, total, fg, background);
    panel_text(16, "Hilos vivos (incluye idle)", label, background);
    panel_number(17, threads, fg, background);
    panel_text(19, "Cambios de contexto", label, background);
    panel_number(20, switches, fg, background);
    panel_text(22, "Llamadas sin permiso", label, background);
    panel_number(23, denied, fg, background);
    panel_text(25, "Actualiza con /panel", label, background);
    panel_text(27, "Halo: ", label, background);
    panel_value(27, 6, halo_name(halo_state), accent, background);
    panel_text(28, "Ultima accion: ", label, background);
    panel_value(28, 15, op_name(last_op), accent, background);
    if last_op != 0 { panel_number(29, last_receipt, fg, background); }
    if halo_state == 5 { panel_text(31, "Destino de datos: NUBE (egreso)", label, background); }
    else { panel_text(31, "Destino de datos: local, sin egreso", label, background); }
}
