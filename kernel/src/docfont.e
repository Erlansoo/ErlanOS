// Fuentes de documentos (DejaVu Sans y Serif, 40x48 por glifo, alfa de 4 bits). Los datos
// los genera scripts/gen_doc_fonts.py en assets/fonts/font_data.h; la lógica es Erlan.
// Sustituye a docfont.h desde el 26/10/2026.
extern c "font_data.h" {
    let doc_advances: [2][224]u16;
    let doc_atlas: [2][215040]u8;
}

fn docfont_glyph(ch: u64) -> u64 { if ch >= 32 && ch < 256 { return ch - 32; } return 63 - 32; }

fn docfont_advance(font: u64, ch: u64) -> u64 {
    var f = font;
    if f >= 2 { f = 0; }
    return doc_advances[f][docfont_glyph(ch)] as u64;
}

fn docfont_alpha(font: u64, ch: u64, x: u64, y: u64) -> u64 {
    if font > 1 || x >= 40 || y >= 48 { return 0; }
    let pixel = docfont_glyph(ch) * 1920 + y * 40 + x;
    let pair = doc_atlas[font][pixel / 2];
    var nibble: u64 = (pair >> 4) as u64;
    if (pixel & 1) != 0 { nibble = (pair & 15) as u64; }
    return nibble * 17;
}
