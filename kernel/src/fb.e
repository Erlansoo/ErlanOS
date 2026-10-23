// L1 — framebuffer lineal. Primer módulo del kernel escrito en Erlan.
// Se compila con erlancompiler; main.c lo usa a través del header generado.

struct Fb {
    base: ptr<u32>;
    width: u64;
    height: u64;
    pitch: u64;      // bytes por fila
    rs: u8;          // desplazamientos de máscara de color
    gs: u8;
    bs: u8;
}

fn fb_init(f: ptr<Fb>, base: ptr<u32>, width: u64, height: u64, pitch: u64, rs: u8, gs: u8, bs: u8) {
    f.base = base;
    f.width = width;
    f.height = height;
    f.pitch = pitch;
    f.rs = rs;
    f.gs = gs;
    f.bs = bs;
}

fn fb_rgb(f: ptr<Fb>, r: u8, g: u8, b: u8) -> u32 {
    return ((r as u32) << (f.rs as u32)) | ((g as u32) << (f.gs as u32)) | ((b as u32) << (f.bs as u32));
}

fn fb_pixel(f: ptr<Fb>, x: i64, y: i64, color: u32) {
    if x < 0 || y < 0 || (x as u64) >= f.width || (y as u64) >= f.height { return; }
    // El framebuffer es memoria de dispositivo: la aritmética de direcciones va en unsafe.
    unsafe {
        let row = ((f.base as ptr<u8>) + (y as u64) * f.pitch) as ptr<u32>;
        row[x as u64] = color;
    }
}

fn fb_rect(f: ptr<Fb>, x: i64, y: i64, w: i64, h: i64, color: u32) {
    for j in y..y + h {
        for i in x..x + w {
            fb_pixel(f, i, j, color);
        }
    }
}

fn fb_clear(f: ptr<Fb>, color: u32) {
    fb_rect(f, 0, 0, f.width as i64, f.height as i64, color);
}

fn fb_circle(f: ptr<Fb>, cx: i64, cy: i64, r: i64, color: u32) {
    for j in -r..=r {
        for i in -r..=r {
            if i * i + j * j <= r * r {
                fb_pixel(f, cx + i, cy + j, color);
            }
        }
    }
}

// Pantalla inicial de ErlanOS: tres puntos centrados (estado idle).
fn fb_draw_idle_dots(f: ptr<Fb>) {
    fb_clear(f, fb_rgb(f, 0x0b, 0x0b, 0x0e));
    let dot = fb_rgb(f, 0xd8, 0xd8, 0xde);
    var r = (f.height / 72) as i64;
    if r < 3 { r = 3; }
    let gap = r * 6;
    let cx = (f.width / 2) as i64;
    let cy = (f.height / 2) as i64;
    fb_circle(f, cx - gap, cy, r, dot);
    fb_circle(f, cx,       cy, r, dot);
    fb_circle(f, cx + gap, cy, r, dot);
}
