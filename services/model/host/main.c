/* Harness de host: carga el GGUF desde disco y ejecuta el model runtime Erlan
 * fuera de ErlanOS. Uso: model_host <modelo.gguf> "<mensaje>" [max_tokens] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "erlan_rt.h"
#include "chat.h"

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "uso: %s modelo.gguf \"mensaje\" [max]\n", argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("modelo"); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    u8 *data = malloc(sz);
    if (fread(data, 1, sz, f) != (size_t)sz) { perror("read"); return 1; }
    fclose(f);
    u64 arena_size = 96ull * 1024 * 1024;
    u8 *arena = malloc(arena_size);
    if (!model_load((u64)(uintptr_t)data, (u64)sz, (u64)(uintptr_t)arena, arena_size)) {
        fprintf(stderr, "model_load fallo\n"); return 1;
    }
    fprintf(stderr, "[host] modelo cargado, arena usada %llu KiB\n", (unsigned long long)(model_arena_used() / 1024));
    u64 max = argc > 3 ? strtoull(argv[3], 0, 10) : 48;
    erlan_su8 msg = { (u8 *)argv[2], strlen(argv[2]) };
    model_chat(msg, max);
    fprintf(stderr, "[host] forward: %llu pasos, %llu ms\n",
            (unsigned long long)model_stats_count(), (unsigned long long)model_stats_ms());
    return 0;
}
