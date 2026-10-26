/* Actual GGUF planner inference; decisions are printed and never executed. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "erlan_rt.h"
#include "chat.h"
#include "planner.h"

/* Ganchos de la plataforma de anfitrión (services/model/host/platform.e, en Erlan): la salida,
 * el registro y el reloj pasan por aquí porque stdout/stderr y CLOCKS_PER_SEC son macros de C. */
#include <time.h>
#include "umath.h"
void host_write(u8 *p, u64 n) { fwrite(p, 1, (size_t)n, stdout); fflush(stdout); }
void host_log(const char *s) { fprintf(stderr, "[model] %s\n", s); }
u64 host_now_ms(void) { return (u64)clock() * 1000 / CLOCKS_PER_SEC; }
bool host_cancelled(void) { return false; }

static u64 plan_request(const char *text) {
    erlan_su8 request = {(u8 *)text, strlen(text)};
    u64 start = model_stats_ms();
    u64 action = model_plan(request);
    printf("PLAN action=%llu tokens=%llu ms=%llu margin=%.3f scores=[",
           (unsigned long long)action, (unsigned long long)model_plan_tokens(),
           (unsigned long long)(model_stats_ms() - start), (double)model_plan_margin());
    for (u64 a = 0; a < 5; a++)
        printf("%s%.3f", a ? "," : "", (double)model_plan_score(a));
    printf("] request=%s\n", text);
    fflush(stdout);
    return action;
}

static int self_test(void) {
    /* Curated smoke cases, not a general measure of intent accuracy. */
    static const struct { const char *text; u64 expected; } cases[] = {
        {"Read system status", 1}, {"Enable focus mode", 2},
        {"Switch to normal mode", 3}, {"Show status panel", 4},
        {"Hello", 0}, {"Delete all files", 0}, {"Tell me a joke", 0},
        {"Do not enable focus mode", 0},
        {"Muestra el estado del sistema", 1}, {"Activa el modo concentracion", 2},
        {"Vuelve al modo normal", 0}, /* Insufficient margin: abstention. */
        {"Muestra el panel de estado", 4}
    };
    int failed = 0;
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        u64 start = model_stats_count();
        u64 action = plan_request(cases[i].text);
        if (action != cases[i].expected || model_plan_tokens() == 0 ||
            model_plan_tokens() > 192 || model_stats_count() - start != model_plan_tokens()) {
            fprintf(stderr, "FAIL expected action=%llu: %s\n",
                    (unsigned long long)cases[i].expected, cases[i].text);
            failed++;
        }
    }
    /* A warm call must agree with a cold replay, including all five logits. */
    u64 warm = plan_request("Enable focus mode");
    u64 warm_steps = model_plan_tokens();
    f32 scores[5];
    for (u64 a = 0; a < 5; a++) scores[a] = model_plan_score(a);
    model_plan_invalidate();
    u64 cold = plan_request("Enable focus mode");
    u64 cold_steps = model_plan_tokens();
    if (warm != cold || warm_steps >= cold_steps) failed++;
    for (u64 a = 0; a < 5; a++) if (scores[a] != model_plan_score(a)) failed++;
    /* Identical inputs reuse all but the last token, restoring final logits. */
    if (plan_request("Enable focus mode") != cold || model_plan_tokens() != 1) failed++;
    for (u64 a = 0; a < 5; a++) if (scores[a] != model_plan_score(a)) failed++;

    char too_long[162], too_many_tokens[161];
    memset(too_long, 'x', 161); too_long[161] = 0;
    for (size_t i = 0; i < 160; i++) too_many_tokens[i] = (char)('0' + i % 10);
    too_many_tokens[160] = 0;
    const char *rejected[] = {"", too_long, too_many_tokens};
    u64 before = model_stats_count();
    for (size_t i = 0; i < sizeof rejected / sizeof rejected[0]; i++) {
        erlan_su8 s = {(u8 *)rejected[i], strlen(rejected[i])};
        if (model_plan(s) != 0 || model_plan_tokens() != 0) failed++;
    }
    if (model_stats_count() != before) failed++;
    printf("PLANNER_SELF_TEST %s failures=%d warm_steps=%llu cold_steps=%llu\n",
           failed ? "FAIL" : "PASS", failed, (unsigned long long)warm_steps,
           (unsigned long long)cold_steps);
    return failed ? 1 : 0;
}

int main(int argc, char **argv) {
#ifdef ERLAN_MATMUL_FORCE_PORTABLE
    math_force_portable(true);   /* ruta sin AVX2 (antes, macro de umath.h) */
#endif

    if (argc < 3) {
        fprintf(stderr, "usage: %s model.gguf request [request ...] | --self-test\n", argv[0]);
        return 2;
    }
    erlan_su8 unloaded = {(u8 *)"Enable focus mode", 17};
    if (model_plan(unloaded) != 0 || model_plan_tokens() != 0) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("model"); return 1; }
    if (fseek(f, 0, SEEK_END) != 0) return 1;
    long sz = ftell(f);
    if (sz <= 0 || fseek(f, 0, SEEK_SET) != 0) return 1;
    u8 *data = malloc((size_t)sz);
    if (!data || fread(data, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    u64 arena_size = 96ull * 1024 * 1024;
    u8 *arena = malloc(arena_size);
    if (!arena || !model_load((u64)(uintptr_t)data, (u64)sz,
                              (u64)(uintptr_t)arena, arena_size)) {
        fprintf(stderr, "model_load failed\n");
        return 1;
    }
    model_plan_invalidate();
    int result = 0;
    if (argc == 3 && strcmp(argv[2], "--self-test") == 0) result = self_test();
    else for (int i = 2; i < argc; i++) plan_request(argv[i]);
    free(arena);
    free(data);
    return result;
}
