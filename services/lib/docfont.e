// Métricas de las fuentes de documentos para los servicios (L2): sólo los avances; el atlas
// de píxeles lo usa el kernel (kernel/src/docfont.e). Datos generados en
// assets/fonts/font_data.h. Sustituye a services/lib/docfont.h desde el 26/10/2026.
extern c "font_data.h" { let doc_advances: [2][224]u16; }

fn docfont_advance(font: u64, ch: u64) -> u64 {
    var f = font;
    if f >= 2 { f = 0; }
    var g: u64 = 63 - 32;
    if ch >= 32 && ch < 256 { g = ch - 32; }
    return doc_advances[f][g] as u64;
}
