#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include "arena.h"
#include "gguf.h"
#include "tokenizer.h"
u64 numeric_cancel_after, numeric_cancel_polls;
void erlan_panic(const char *s) { fprintf(stderr, "%s\n", s); abort(); }
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    FILE *f = fopen(argv[1], "rb"); if (!f) return 2;
#ifdef _WIN32
    _fseeki64(f, 0, SEEK_END); int64_t size = _ftelli64(f); _fseeki64(f, 0, SEEK_SET);
#else
    fseeko(f, 0, SEEK_END); int64_t size = ftello(f); fseeko(f, 0, SEEK_SET);
#endif
    if (size <= 0) return 2;
    u8 *data = malloc((size_t)size), *memory = malloc(32u * 1024u * 1024u);
    if (!data || !memory || fread(data, 1, (size_t)size, f) != (size_t)size) return 2;
    fclose(f); arena_init((u64)(uintptr_t)memory, 32u * 1024u * 1024u);
    if (!gguf_open((u64)(uintptr_t)data, (u64)size) || !tokenizer_init()) return 3;
    char line[8194]; u8 input[4096], decoded[8192]; u32 tokens[4096];
    while (fgets(line, sizeof line, stdin)) {
        size_t n = strcspn(line, "\r\n"); if (n % 2 || n > sizeof(input) * 2) return 4;
        for (size_t i = 0; i < n / 2; ++i) {
            char pair[3] = {line[2*i], line[2*i+1], 0};
            input[i] = (u8)strtoul(pair, NULL, 16);
        }
        erlan_su8 in = {input, n / 2}; erlan_su32 out = {tokens, 4096};
        u64 count = tokenizer_encode(in, out), dn = 0;
        for (u64 i = 0; i < count; ++i) {
            erlan_su8 dst = {decoded + dn, sizeof(decoded) - dn};
            dn += tokenizer_decode(tokens[i], dst);
        }
        if (dn != n / 2 || memcmp(decoded, input, dn)) { fprintf(stderr, "roundtrip failed\n"); return 5; }
        putchar('[');
        for (u64 i = 0; i < count; ++i) printf("%s%u", i ? "," : "", (unsigned)tokens[i]);
        puts("]"); fflush(stdout);
    }
    free(memory); free(data); return 0;
}
