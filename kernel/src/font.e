// Fuente 8x16 de la consola (Consolas, Latin-1 32..255). Los bits los genera
// scripts/gen_font.ps1 en font8x16.h (una tabla de datos en C); la lógica es Erlan.
// Sustituye a font.h desde el 26/10/2026.
extern c "font8x16.h" { let font8x16: [224][16]u8; }

// Fila `row` (0..15) del glifo Latin-1; bit 7 = píxel izquierdo.
fn font_row(ch: u8, row: u64) -> u8 {
    var c = ch;
    if c < 32 || (c >= 127 && c < 160) { c = '?'; }
    return font8x16[(c - 32) as u64][row & 15];
}
