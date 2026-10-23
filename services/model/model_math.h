#ifndef ERLAN_MODEL_MATH_H
#define ERLAN_MODEL_MATH_H
#include "erlan_rt.h"
#ifndef ERLAN_DEF_erlan_sf32
#define ERLAN_DEF_erlan_sf32
ERLAN_SLICE(erlan_sf32, f32)
#endif
#ifndef ERLAN_DEF_erlan_su8
#define ERLAN_DEF_erlan_su8
ERLAN_SLICE(erlan_su8, u8)
#endif
void matmul(erlan_sf32 out, erlan_sf32 x, u64 w, u32 type, u64 rows, u64 cols);
void dequant_row(erlan_sf32 out, u64 w, u32 type, u64 row, u64 cols);
u64 name_layer(erlan_su8 buf, u64 layer, const char *suffix);
u64 name_plain(erlan_su8 buf, const char *name);
bool tensor_valid(u64 tensor, u64 cols, u64 rows, bool vector, bool f32_only);
#endif
