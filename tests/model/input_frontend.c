/* Execute the actual model service input loop, stubbing inference and syscalls. */
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "frontend_stubs.h"
void model_service_entry(void);
static jmp_buf finished;
static u8 events[1024]; static size_t event_n, event_pos, submissions, repaints;
static char output[8192]; static size_t output_n;
static void push(u8 c) { events[event_n++] = c; }
static void type(const char *s) { while (*s) push((u8)*s++); }
void erlan_panic(const char *s) { fprintf(stderr, "%s\n", s); abort(); }
u8 platform_getkey(void) {
    // Readiness and ignored Escape/Backspace leave a completely clean canvas.
    if (event_pos <= 2) assert(output_n == 0);
    if (event_pos == event_n) longjmp(finished, 1);
    return events[event_pos++];
}
void platform_write(u8 *p, u64 n) {
    assert(output_n + n < sizeof(output));
    memcpy(output + output_n, p, n); output_n += n;
    for (u64 i = 0; i < n; ++i) if (p[i] == 15) ++repaints;
}
void platform_log(const char *s) { (void)s; }
void platform_clear_cancel(void) {}
void platform_halo(u64 n) { (void)n; }
void platform_sleep_ms(u64 n) { (void)n; assert(0); }
u64 platform_map_module(u64 n) { (void)n; return 1; }
u64 platform_module_size(u64 n) { (void)n; return 1; }
u64 platform_map_anon(u64 n) { (void)n; return 1; }
u64 ulog(const char *s) { (void)s; return 0; }
void ulog_num(const char *s, u64 n) { (void)s; (void)n; }
void uexit(u64 n) { (void)n; assert(0); }
bool model_load(u64 a, u64 b, u64 c, u64 d) { (void)a; (void)b; (void)c; (void)d; return true; }
u64 model_arena_used(void) { return 1; }
bool model_warmup(void) { assert(output_n == 0); return true; }
u64 model_chat_context(void) { return 1; }
bool mind_handle(erlan_su8 line) { (void)line; return false; }
bool document_command(erlan_su8 line) { (void)line; return false; }
bool document_editing(void) { return false; }
void document_key(u8 c) { (void)c; assert(0); }
void workspace_client_init(void) {}
void workspace_poll(void) {}
bool workspace_key(u8 c) { (void)c; return false; }
bool workspace_command(erlan_su8 b) { (void)b; return false; }
bool workspace_chat_route(erlan_su8 b) { (void)b; return false; }
void workspace_chat(erlan_su8 b) { model_chat(b,64); }
void mind_help(void) {}
void model_plan_invalidate(void) {}
erlan_su8 gguf_model_name(void) { return (erlan_su8){(u8 *)"test", 4}; }
u64 model_chat(erlan_su8 line, u64 limit) {
    assert(limit == 64);
    const char *expected[] = {"def suma(a, b):\n    return a + b", "abcd", NULL, "OK"};
    assert(submissions < 4);
    if (submissions == 2) {
        assert(line.len == 240);
        for (u64 i = 0; i < line.len; ++i) assert(line.ptr[i] == 'q');
    } else {
        assert(line.len == strlen(expected[submissions]));
        assert(memcmp(line.ptr, expected[submissions], line.len) == 0);
    }
    ++submissions; return 0;
}
int main(void) {
    push(27); push(8); // no prompt until the first useful input
    type("def suma(a, b):"); push(11); type("    return a + b"); push(10);
    type("abc"); push(11); push(8); type("dX"); push(8); push(10);
    for (int i = 0; i < 240; ++i) push('q');
    push(11); push('z'); push(10); // full input rejects both an LF and a byte
    type("discard me"); push(27); type("OK"); push(10);
    if (!setjmp(finished)) model_service_entry();
    assert(submissions == 4 && repaints == 2);
    const char prefix[] = "\x0e> def suma(a, b):\n    return a + b";
    assert(strncmp(output, prefix, sizeof(prefix) - 1) == 0);
    puts("INPUT_FRONTEND PASS clean startup, lazy prompt, multiline exact, Enter submit, newline backspace, 240-byte cap, Escape");
    return 0;
}
