/* SmolLM ASCII pre-tokenizer regression against explicit upstream regex chunks.
 * IDs and merges come from the user's GGUF, never hardcoded into the runtime.
 * Reference: ggml-org/llama.cpp src/llama-vocab.cpp, LLAMA_VOCAB_PRE_TYPE_SMOLLM
 * https://github.com/ggml-org/llama.cpp/blob/master/src/llama-vocab.cpp */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "arena.h"
#include "gguf.h"
#include "tokenizer.h"
u64 numeric_cancel_after, numeric_cancel_polls;
void erlan_panic(const char *s) { fprintf(stderr, "%s\n", s); abort(); }

static erlan_su8 bytes(const char *s) { erlan_su8 b = {(u8 *)s, strlen(s)}; return b; }

static void check(const char *text, const char *const *chunks, size_t nc) {
    u32 got[256], expected[256];
    erlan_su32 actual = {got, 256}, ref = {expected, 256};
    u64 n = tokenizer_encode(bytes(text), actual), rn = 0;
    for (size_t i = 0; i < nc; ++i) {
        erlan_su8 chunk = bytes(chunks[i]);
        rn = bpe_chunk(chunk, 0, chunk.len, ref, rn);
    }
    if (n != rn || memcmp(got, expected, n * sizeof(u32))) {
        fprintf(stderr, "TOKENIZER FAIL text=%s actual=", text);
        for (u64 i = 0; i < n; ++i) fprintf(stderr, "%llu,", (unsigned long long)got[i]);
        fprintf(stderr, " expected=");
        for (u64 i = 0; i < rn; ++i) fprintf(stderr, "%llu,", (unsigned long long)expected[i]);
        fputc('\n', stderr); abort();
    }
}

#define CHECK(text, ...) do { const char *chunks[] = {__VA_ARGS__}; check(text, chunks, sizeof(chunks)/sizeof(chunks[0])); } while (0)

int main(int argc, char **argv) {
    assert(argc == 2);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    assert(fseek(f, 0, SEEK_END) == 0); long size = ftell(f); assert(size > 0); rewind(f);
    u8 *data = malloc((size_t)size), *memory = malloc(32 * 1024 * 1024);
    assert(data && memory && fread(data, 1, (size_t)size, f) == (size_t)size); fclose(f);
    arena_init((u64)memory, 32 * 1024 * 1024);
    assert(gguf_open((u64)data, (u64)size) && tokenizer_init());
    CHECK("I'm", "I", "'m");
    CHECK("it's", "it", "'s");
    CHECK("don't", "don", "'t");
    CHECK("we're", "we", "'re");
    CHECK("they've", "they", "'ve");
    CHECK("you'll", "you", "'ll");
    CHECK("I'd", "I", "'d");
    CHECK("I'mabc", "I", "'m", "abc");
    CHECK("I'M", "I", "'", "M"); // upstream SMOLLM regex is case-sensitive
    CHECK(" 'm", " '", "m"); // leading space belongs to punctuation alternative
    CHECK("'", "'");
    CHECK("'r", "'", "r");
    CHECK("\n\n", "\n\n");
    CHECK("a\n\n", "a", "\n\n");
    CHECK("a\n\nb", "a", "\n", "\n", "b");
    CHECK("a\n\n\nb", "a", "\n\n", "\n", "b");
    CHECK("a \t\n b", "a", " \t\n", " b");
    CHECK("\r\n\r\n", "\r\n\r\n");
    CHECK(" \v\f\t", " \v\f\t");
    CHECK("a  b", "a", " ", " b");
    CHECK("a   ", "a", "   ");
    CHECK("a 12", "a", " ", "1", "2");
    CHECK("a  12", "a", "  ", "1", "2");
    CHECK("\n\n1", "\n\n", "1");
    CHECK("1  2", "1", "  ", "2");
    CHECK("Who are you?", "Who", " are", " you", "?");
    CHECK("system\nYou are Erlan.", "system", "\n", "You", " are", " Erlan", ".");
    u32 ids[16]; erlan_su32 out = {ids, 16};
    assert(tokenizer_encode(bytes("I'm"), out) == 2);
    assert(ids[0] == tokenizer_id("I") && ids[1] == tokenizer_id("'m"));
    assert(tokenizer_encode(bytes("\n\n"), out) == 1);
    assert(ids[0] == tokenizer_id("\xC4\x8A\xC4\x8A")); // GPT-2 byte alphabet for LF LF
    assert(tokenizer_id("<|im_start|>") < tokenizer_n_tokens());
    puts("TOKENIZER PASS 27 SmolLM ASCII cases, contraction/newline IDs from GGUF");
    free(memory); free(data);
    return 0;
}
