#ifndef ERLAN_DOCFONT_H
#define ERLAN_DOCFONT_H
#include "../../assets/fonts/font_data.h"
static inline uint64_t docfont_advance(uint64_t font, uint64_t ch) {
    return doc_advances[font < 2 ? font : 0][(ch >= 32 && ch < 256 ? ch : 63) - 32];
}
static inline uint64_t docfont_alpha(uint64_t font, uint64_t ch, uint64_t x, uint64_t y) {
    if (font > 1 || x >= 40 || y >= 48) return 0;
    uint64_t pixel = ((ch >= 32 && ch < 256 ? ch : 63) - 32) * 1920 + y * 40 + x;
    uint8_t pair = doc_atlas[font][pixel / 2];
    return ((pixel & 1) ? (pair & 15) : (pair >> 4)) * 17;
}
#endif
