/* Acceso a la fuente 8x16 para el código Erlan (console.e). */
#ifndef ERLANOS_FONT_H
#define ERLANOS_FONT_H
#include "font8x16.h"
/* Fila `row` (0..15) del glifo Latin-1; bit 7 = píxel izquierdo. */
static inline u8 font_row(u8 ch, u64 row) {
    if (ch < 32 || (ch >= 127 && ch < 160)) ch = '?';
    return font8x16[ch - 32][row & 15];
}
#endif
