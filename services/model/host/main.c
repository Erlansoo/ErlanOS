/* Harness de host: carga el GGUF desde disco y ejecuta el model runtime Erlan
 * fuera de ErlanOS. Uso: model_host <modelo.gguf> "<mensaje>" [max_tokens] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "erlan_rt.h"
#include "chat.h"

/* Ganchos de la plataforma de anfitrión (services/model/host/platform.e, en Erlan): la salida,
 * el registro y el reloj pasan por aquí porque stdout/stderr y CLOCKS_PER_SEC son macros de C. */
#include <time.h>
#include "umath.h"
void host_write(u8 *p, u64 n) { fwrite(p, 1, (size_t)n, stdout); fflush(stdout); }
void host_log(const char *s) { fprintf(stderr, "[model] %s\n", s); }
u64 host_now_ms(void) { return (u64)clock() * 1000 / CLOCKS_PER_SEC; }
bool host_cancelled(void) { return false; }

int main(int argc, char **argv) {
#ifdef ERLAN_MATMUL_FORCE_PORTABLE
    math_force_portable(true);   /* ruta sin AVX2 (antes, macro de umath.h) */
#endif
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
