/* Benchmarks execute the real transpiled Erlan Q8 kernels with fixed inputs.
 * Optional GGUF replay compares complete logits and tests partial cancellation. */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "llama.h"
#include "arena.h"
#include "gguf.h"
#include "matmul_reference.h"
#include "qwen35.h"
#include "umath.h"
u64 numeric_cancel_after, numeric_cancel_polls;
static volatile f32 sink;

static void check_cpu_guard(void) {
    const u32 required = (1u << 26) | (1u << 27) | (1u << 28);
    assert(math_avx2_state_ok(7, required, 1u << 5, 6));
    assert(!math_avx2_state_ok(6, required, 1u << 5, 6));
    for (u32 bit = 26; bit <= 28; ++bit) assert(!math_avx2_state_ok(7, required & ~(1u << bit), 1u << 5, 6));
    assert(!math_avx2_state_ok(7, required, 0, 6));
    assert(!math_avx2_state_ok(7, required, 1u << 5, 2));
    assert(!math_avx2_state_ok(7, required, 1u << 5, 4));
    assert(matmul_fast_enabled() == math_cpu_avx2());
    printf("AVX2_GUARD PASS selected=%s missing_prerequisites_rejected=true\n", matmul_fast_enabled() ? "avx2" : "portable");
}

static void check_delta(void) {
    enum { N = 8 };
    f32 state[N*N], q[N], k[N], v[N], out[N];
    double reference[N*N];
    for (int i = 0; i < N*N; ++i) state[i] = (f32)(reference[i] = (i % 11 - 5) / 128.0);
    double worst = 0;
    for (int step = 0; step < 20; ++step) {
        for (int i = 0; i < N; ++i) {
            q[i] = ((i * 3 + step) % 13 - 6) / 32.0f;
            k[i] = ((i * 7 + step) % 11 - 5) / 16.0f;
            v[i] = ((i * 5 + step) % 17 - 8) / 8.0f;
        }
        f32 decay = 0.55f + step / 64.0f, beta = 0.1f + step / 32.0f;
        q35_delta((erlan_sf32){state,N*N}, (erlan_sf32){q,N}, (erlan_sf32){k,N},
                  (erlan_sf32){v,N}, (erlan_sf32){out,N}, N, decay, beta);
        for (int j = 0; j < N; ++j) {
            double memory = 0, actual = 0;
            for (int i = 0; i < N; ++i) { reference[i*N+j] *= decay; memory += reference[i*N+j] * k[i]; }
            double delta = (v[j] - memory) * beta;
            for (int i = 0; i < N; ++i) { reference[i*N+j] += k[i] * delta; actual += reference[i*N+j] * q[i]; }
            double error = fabs(out[j] - actual); if (error > worst) worst = error;
            assert(isfinite(out[j]) && error < 0.000003);
        }
        for (int i = 0; i < N*N; ++i) assert(fabs(state[i] - reference[i]) < 0.000003);
    }
    printf("GATED_DELTA PASS nonzero_state=true recurrent_steps=20 max_abs=%.8g\n", worst);
}

void erlan_panic(const char *s) { fprintf(stderr, "%s\n", s); abort(); }

static void check_fallbacks(void) {
    f32 input[7] = {-1, -0.5f, 0, 0.25f, 0.5f, 0.75f, 1};
    f32 weights[21], got[3], expected[3];
    u16 halves[21];
    for (size_t i = 0; i < 21; ++i) { weights[i] = (f32)i / 17; halves[i] = (u16)(0x3400 + i * 32); }
    erlan_sf32 in = {input, 7}, actual = {got, 3}, ref = {expected, 3};
    reference_matmul(ref, in, (u64)weights, 0, 3, 7);
    matmul(actual, in, (u64)weights, 0, 3, 7);
    assert(memcmp(got, expected, sizeof(got)) == 0);
    reference_matmul(ref, in, (u64)halves, 1, 3, 7);
    matmul(actual, in, (u64)halves, 1, 3, 7);
    assert(memcmp(got, expected, sizeof(got)) == 0);
    const u64 cols = 16416, blocks = cols / 32;
    f32 *wide = malloc(cols * sizeof(f32)); u8 *quant = malloc(blocks * 34);
    assert(wide && quant);
    for (u64 i = 0; i < cols; ++i) wide[i] = 0.25f;
    for (u64 b = 0; b < blocks; ++b) {
        quant[b * 34] = 0; quant[b * 34 + 1] = 0x3c; // scale = 1
        memset(quant + b * 34 + 2, 1, 32);
    }
    in.ptr = wide; in.len = cols;
    matmul(actual, in, (u64)quant, 8, 1, cols);
    assert(got[0] == (f32)cols * 0.25f);
    free(wide); free(quant);
    puts("MATMUL_FALLBACK PASS F32/F16 and Q8 width above integer scratch limit");
}

static double bench(bool reference, erlan_sf32 out, erlan_sf32 input, u8 *w,
                    u64 rows, u64 cols, unsigned repeats) {
    clock_t start = clock();
    for (unsigned i = 0; i < repeats; ++i) {
        if (reference) reference_matmul(out, input, (u64)w, 8, rows, cols);
        else matmul(out, input, (u64)w, 8, rows, cols);
        sink = out.ptr[i % rows];
    }
    return (double)(clock() - start) * 1000.0 / CLOCKS_PER_SEC;
}

static void case_q8(u64 rows, u64 cols, unsigned repeats) {
    const size_t blocks = rows * cols / 32;
    u8 *storage = malloc(blocks * 34 + 1), *w = storage + 1; // deliberately unaligned
    f32 *x = malloc(cols * sizeof(*x));
    f32 *got = malloc(rows * sizeof(*got)), *expected = malloc(rows * sizeof(*expected));
    assert(storage && x && got && expected);
    for (size_t b = 0; b < blocks; ++b) {
        const u16 half = (u16)(0x1400 + (b * 137) % 0x2000);
        w[b * 34] = half & 255; w[b * 34 + 1] = half >> 8;
        for (size_t i = 0; i < 32; ++i) w[b * 34 + 2 + i] = (u8)((b * 71 + i * 23) & 255);
    }
    for (size_t i = 0; i < cols; ++i) x[i] = ((int)((i * 59 + 17) % 1021) - 510) / 511.0f;
    erlan_sf32 in = {x, cols}, actual = {got, rows}, ref = {expected, rows};
    reference_matmul(ref, in, (u64)w, 8, rows, cols);
    matmul(actual, in, (u64)w, 8, rows, cols);
    double max_error = 0;
    for (size_t r = 0; r < rows; ++r) {
        double error = fabs((double)got[r] - expected[r]);
        if (error > max_error) max_error = error;
        if (!isfinite(got[r]) || error > 0.001 + 0.00001 * fabs(expected[r])) {
            fprintf(stderr, "NUMERIC row=%zu got=%.9g expected=%.9g abs=%.9g\n", r, got[r], expected[r], error);
            abort();
        }
    }
    double old_ms[3], new_ms[3];
    for (size_t n = 0; n < 3; ++n) {
        // Alternate order to reduce systematic warmup or CPU frequency bias.
        if (n % 2) {
            new_ms[n] = bench(false, actual, in, w, rows, cols, repeats);
            old_ms[n] = bench(true, ref, in, w, rows, cols, repeats);
        } else {
            old_ms[n] = bench(true, ref, in, w, rows, cols, repeats);
            new_ms[n] = bench(false, actual, in, w, rows, cols, repeats);
        }
    }
    for (size_t i = 0; i < 3; ++i) for (size_t j = i + 1; j < 3; ++j) {
        if (old_ms[i] > old_ms[j]) { double t = old_ms[i]; old_ms[i] = old_ms[j]; old_ms[j] = t; }
        if (new_ms[i] > new_ms[j]) { double t = new_ms[i]; new_ms[i] = new_ms[j]; new_ms[j] = t; }
    }
    printf("MATMUL rows=%llu cols=%llu repeats=%u reference_ms=%.3f optimized_ms=%.3f speedup=%.3f max_abs=%.8g\n",
        (unsigned long long)rows, (unsigned long long)cols, repeats,
        old_ms[1], new_ms[1], old_ms[1] / new_ms[1], max_error);
    free(storage); free(x); free(got); free(expected);
}

static void replay(const char *model, const char *dump, bool prefill) {
    FILE *f = fopen(model, "rb"); assert(f);
    assert(fseek(f, 0, SEEK_END) == 0); long size = ftell(f); assert(size > 0); rewind(f);
    u8 *data = malloc((size_t)size), *memory = malloc(96 * 1024 * 1024);
    assert(data && memory && fread(data, 1, (size_t)size, f) == (size_t)size); fclose(f);
    arena_init((u64)memory, 96 * 1024 * 1024);
    assert(gguf_open((u64)data, (u64)size) && llama_init());
    numeric_cancel_after = 3; numeric_cancel_polls = 0;
    llama_forward(42, 0);
    assert(llama_forward_cancelled() && llama_forward_count() == 0 && numeric_cancel_polls == 4);
    llama_reset_context();
    numeric_cancel_polls = 0;
    llama_prefill(42, 0);
    assert(llama_forward_cancelled() && llama_forward_count() == 0 && numeric_cancel_polls == 4);
    llama_reset_context();
    numeric_cancel_after = 0; numeric_cancel_polls = 0;
    f = fopen(dump, "wb"); assert(f);
    const u64 tokens[] = {1, 9707, 0, 42, 4865, 198, 1234, 17};
    for (size_t p = 0; p < sizeof(tokens)/sizeof(tokens[0]); ++p) {
        bool need_logits = !prefill || p == sizeof(tokens)/sizeof(tokens[0]) - 1;
        if (need_logits) llama_forward(tokens[p], p);
        else llama_prefill(tokens[p], p);
        assert(!llama_forward_cancelled() && llama_forward_count() == p + 1);
        if (!need_logits) continue;
        printf("FORWARD pos=%zu argmax=%llu\n", p, (unsigned long long)llama_argmax());
        for (u64 i = 0; i < llama_vocab(); ++i) {
            f32 value = llama_logit(i); assert(isfinite(value));
            assert(fwrite(&value, sizeof(value), 1, f) == 1);
        }
    }
    fclose(f);
    printf("FORWARD_SUMMARY steps=%llu ms=%llu vocab=%llu cancellation=PASS prefill=%d\n",
        (unsigned long long)llama_forward_count(), (unsigned long long)llama_forward_ms(),
        (unsigned long long)llama_vocab(), (int)prefill);
    if (gguf_architecture() == 3) {
        f32 *last = malloc(llama_vocab() * sizeof(*last)); assert(last);
        for (u64 i = 0; i < llama_vocab(); ++i) last[i] = llama_logit(i);
        const u64 count = llama_forward_count();
        // Position alone cannot rewind recurrent state. A rejected rewind also
        // invalidates subsequent continuation until the caller resets it.
        llama_forward(tokens[0], 0); assert(llama_forward_cancelled() && llama_forward_count() == count);
        llama_forward(tokens[0], 8); assert(llama_forward_cancelled() && llama_forward_count() == count);
        llama_reset_context();
        for (u64 p = 0; p < 8; ++p) {
            if (p < 7) llama_prefill(tokens[p], p); else llama_forward(tokens[p], p);
            assert(!llama_forward_cancelled());
        }
        for (u64 i = 0; i < llama_vocab(); ++i) {
            f32 value = llama_logit(i); assert(memcmp(&last[i], &value, sizeof(value)) == 0);
        }
        free(last);
        puts("RECURRENT_RESET PASS rewind_rejected=true reset_replay_bit_exact=true");
    }
    free(memory); free(data);
}

int main(int argc, char **argv) {
    if (argc >= 3) replay(argv[1], argv[2], argc >= 4 && strcmp(argv[3], "prefill") == 0);
    else {
        check_cpu_guard();
        check_delta();
        check_fallbacks();
        case_q8(576, 576, 192);
        case_q8(1536, 576, 72);
        case_q8(576, 1536, 72);
        case_q8(896, 4864, 16); // Qwen2.5 FFN down projection, persistent Q20 scratch
        case_q8(1536, 8960, 5); // Qwen2.5-Coder-1.5B FFN down no longer falls back
        case_q8(49152, 576, 3);
    }
    return 0;
}
