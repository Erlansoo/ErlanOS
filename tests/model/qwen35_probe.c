/* Exact-ID replay for comparison against an independent llama.cpp process. */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "arena.h"
#include "gguf.h"
#include "llama.h"
u64 numeric_cancel_after, numeric_cancel_polls;
void erlan_panic(const char *s) { fprintf(stderr, "%s\n", s); abort(); }
int main(int argc, char **argv) {
    assert(argc == 4);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    assert(fseek(f, 0, SEEK_END) == 0); long size = ftell(f); assert(size > 0); rewind(f);
    u8 *data = malloc((size_t)size), *arena = malloc(192 * 1024 * 1024);
    assert(data && arena && fread(data, 1, size, f) == (size_t)size); fclose(f);
    arena_init((u64)arena, 192 * 1024 * 1024);
    assert(gguf_open((u64)data, (u64)size) && llama_init());
    f = fopen(argv[2], "r"); assert(f);
    unsigned long long first, token; assert(fscanf(f, "%llu", &first) == 1);
    FILE *dump = fopen(argv[3], "wb"); assert(dump);
    u64 pos = 0;
    while (fscanf(f, "%llu", &token) == 1) {
        assert(pos < llama_ctx());
        if (pos < first) llama_prefill(token, pos); else llama_forward(token, pos);
        assert(!llama_forward_cancelled());
        if (pos >= first) {
            printf("REFERENCE_PROBE pos=%llu top1=%llu\n", (unsigned long long)pos, (unsigned long long)llama_argmax());
            fflush(stdout);
            for (u64 i = 0; i < llama_vocab(); ++i) {
                f32 value = llama_logit(i); assert(isfinite(value));
                assert(fwrite(&value, sizeof(value), 1, dump) == 1);
            }
        }
        ++pos;
    }
    fclose(f); fclose(dump);
    printf("REFERENCE_PROBE_DONE steps=%llu ms=%llu arena=%llu\n", (unsigned long long)pos,
           (unsigned long long)llama_forward_ms(), (unsigned long long)arena_used());
    free(arena); free(data);
    return 0;
}
