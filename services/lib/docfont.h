#ifndef ERLAN_DOCFONT_H
#define ERLAN_DOCFONT_H
#define DOC_FONT_METRICS_ONLY
#include "../../assets/fonts/font_data.h"
static inline uint64_t docfont_advance(uint64_t font, uint64_t ch) {
    return doc_advances[font < 2 ? font : 0][(ch >= 32 && ch < 256 ? ch : 63) - 32];
}
#endif
