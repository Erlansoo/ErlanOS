#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "erlan_rt.h"
#include "chat.h"
#include "gguf.h"
#include "tokenizer.h"
#include <time.h>
#include "umath.h"
u64 host_now_ms(void) { return (u64)clock() * 1000 / CLOCKS_PER_SEC; }

static bool cancel_sticky, cancel_on_output, capture, generating;
static u64 cancel_checks, cancel_at = UINT64_MAX;
static char answer[4096];

static void json_string(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 0x20) printf("\\u%04x", (unsigned)*p);
        else putchar(*p);
    }
    putchar('"');
}

bool host_cancelled(void) {
    if (++cancel_checks >= cancel_at) cancel_sticky = true;
    return cancel_sticky;
}
void host_write(u8 *p, u64 n) {
    fwrite(p, 1, (size_t)n, stdout); fflush(stdout);
    if (cancel_on_output && generating && n) cancel_sticky = true;
}
void host_log(const char *s) {
    fprintf(stderr, "[model] %s\n", s);
    if (!strcmp(s, "chat: begin")) generating = false;
    if (!strcmp(s, "chat: generating")) generating = true;
    if (!strcmp(s, "chat: answer begin")) { capture = true; answer[0] = 0; }
    else if (!strcmp(s, "chat: answer end")) capture = false;
    else if (capture) snprintf(answer, sizeof answer, "%s", s);
}
static void uncancel(void) {
    cancel_sticky = false; cancel_on_output = false;
    cancel_checks = 0; cancel_at = UINT64_MAX;
}
static u64 turn(const char *s, u64 max) {
    erlan_su8 msg = {(u8 *)s, strlen(s)};
    fprintf(stderr, "[host] USER %s\n", s);
    u64 n = model_chat(msg, max);
    fprintf(stderr, "[host] RESULT tokens=%llu status=%llu stop=%llu context=%llu\n",
        (unsigned long long)n, (unsigned long long)model_chat_status(),
        (unsigned long long)model_chat_stop_token(), (unsigned long long)model_chat_context());
    printf("\n[host-json] {\"user\":"); json_string(s);
    printf(",\"answer\":"); json_string(answer);
    printf(",\"tokens\":%llu,\"status\":%llu,\"context\":%llu}\n",
        (unsigned long long)n, (unsigned long long)model_chat_status(),
        (unsigned long long)model_chat_context());
    fflush(stdout);
    return n;
}
int main(int argc, char **argv) {
#ifdef ERLAN_MATMUL_FORCE_PORTABLE
    math_force_portable(true);   /* ruta sin AVX2 (antes, macro de umath.h) */
#endif
    if (argc < 2) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
#ifdef _WIN32
    if (_fseeki64(f, 0, SEEK_END)) return 1;
    int64_t size = _ftelli64(f);
    if (size <= 0 || _fseeki64(f, 0, SEEK_SET)) return 1;
#else
    if (fseeko(f, 0, SEEK_END)) return 1;
    int64_t size = (int64_t)ftello(f);
    if (size <= 0 || fseeko(f, 0, SEEK_SET)) return 1;
#endif
    if ((uint64_t)size > SIZE_MAX) return 1;
    const size_t arena_size = 192u * 1024u * 1024u;
    u8 *data = malloc((size_t)size), *arena = malloc(arena_size);
    if (!data || !arena || fread(data, 1, (size_t)size, f) != (size_t)size) return 1;
    fclose(f);
    if (!model_load((u64)(uintptr_t)data, (u64)size, (u64)(uintptr_t)arena, arena_size)) return 1;
    if (argc == 4 && !strcmp(argv[2], "--tokens")) {
        erlan_su8 msg = {(u8 *)argv[3], strlen(argv[3])};
        u64 n = chat_prompt(msg);
        printf("TOKENS");
        for (u64 i = 0; i < n; i++) printf(" %llu", (unsigned long long)model_chat_prompt_token(i));
        printf("\n");
        return 0;
    }
    model_warmup();   /* como en ErlanOS: el sistema se prellena antes del primer turno */
    if (argc == 3 && !strcmp(argv[2], "--semantic")) {
        // Actual failing user transcript, followed by independent skill checks.
        const char *conversation[] = {"hola", "me llamo erlan, quien eres tu?",
            "como te llamas tu?", "quien eres?", "wtf?"};
        for (size_t i = 0; i < sizeof conversation / sizeof conversation[0]; ++i)
            turn(conversation[i], 64);
        model_reset_context();
        turn("Me llamo Lucia. Recuerda mi nombre.", 64);
        turn("Como me llamo?", 64);
        model_reset_context();
        turn("Cuanto es 2 + 2? Responde solo con el numero.", 64);
        model_reset_context();
        turn("Cuenta del 1 al 5. Responde solo con esos cinco numeros.", 64);
        model_reset_context();
        turn("Escribe solo codigo Python: una funcion suma(a, b) que devuelve a + b.", 64);
        free(arena); free(data);
        return 0; // The Python semantic checker evaluates content, not this exit.
    }
    if (argc > 2 && strcmp(argv[2], "--controls")) {
        for (int i = 2; i < argc; i++) turn(argv[i], 64);
        return 0;
    }
    int fails = 0;
    if (tokenizer_id("<|im_start|>") >= tokenizer_n_tokens() || tokenizer_id("<|im_end|>") >= tokenizer_n_tokens()) fails++;
    if (tokenizer_id("<|endoftext|>") >= tokenizer_n_tokens()) fails++;
    // Cancel part-way through the first transformer pass; no output or stale KV.
    cancel_at = 5;
    u64 before = model_stats_count();
    if (turn("hola", 32) != 0 || model_chat_status() != 2 || model_chat_context() != 0 || model_stats_count() != before) fails++;
    uncancel();
    u64 n = turn("hola", 32);
    if (!n || n > 32 || model_chat_status() != 0 || model_chat_stop_token() != tokenizer_id("<|im_end|>")) fails++;
    char greeting[4096]; snprintf(greeting, sizeof greeting, "%s", answer);
    // A cap must report truncation and close the assistant role for the next turn.
    if (turn("Escribe los numeros del uno al cien sin parar.", 1) != 1 || model_chat_status() != 1) fails++;
    model_reset_context(); uncancel(); cancel_on_output = true;
    if (turn("hola", 32) != 1 || model_chat_status() != 2 || model_chat_context() != 0) fails++;
    uncancel();
    if (!turn("hola", 32) || model_chat_status() != 0 || strcmp(answer, greeting)) fails++;
    char oversized[258]; memset(oversized, 'x', 257); oversized[257] = 0;
    before = model_stats_count();
    if (turn(oversized, 32) != 0 || model_chat_status() != 3) fails++;
    if (turn("", 32) != 0 || model_chat_status() != 3) fails++;
    if (turn("hola", 0) != 0 || model_chat_status() != 3) fails++;
    if (model_stats_count() != before) fails++;
    fprintf(stderr, "CHAT_CONTROL_TEST %s failures=%d\n", fails ? "FAIL" : "PASS", fails);
    free(arena); free(data);
    return fails ? 1 : 0;
}
