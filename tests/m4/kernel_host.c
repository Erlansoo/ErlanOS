/* Mechanical M4 boundary tests using real transpiled syscall/system/vmm/console.
 * Hardware, processes and IPC are stubs; guest integration is tested separately. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "syscall.h"
#include "system.h"
#include "vmm.h"
#include "console.h"
#include "keyboard.h"
#include "font.h"

static _Alignas(4096) u64 pml4[512], pdpt[512], pd[512], pt[512];
static u32 kinds[16], rights[16];
static u64 objects[16];
static u64 recv_calls, send_calls;

void erlan_panic(const char *msg) { fprintf(stderr, "%s\n", msg); abort(); }
void kpanic(const char *msg) { erlan_panic(msg); }
void kprintf(const char *fmt, ...) { (void)fmt; }
void serial_putc(char c) { (void)c; }
u64 cpu_read_cr3(void) { return (u64)pml4; }
void cpu_write_cr3(u64 n) { (void)n; }
void cpu_invlpg(u64 n) { (void)n; }
u64 pmm_phys_to_virt(u64 n) { return n; }
u64 pmm_alloc_page(void) { abort(); }
void pmm_free_page(u64 n) { (void)n; }
u64 pmm_free_count(void) { return 1234; }
u64 pmm_total_count(void) { return 2048; }
u64 pit_uptime_ms(void) { return 42000; }
u64 pit_ticks(void) { return 4200; }
u64 sched_current_id(void) { return 1; }
u64 sched_current_cr3(void) { return (u64)pml4; }
u64 sched_live_count(void) { return 3; }
u64 sched_switches(void) { return 77; }
const char *sched_thread_name(u64 id) { (void)id; return "test"; }
bool sched_check_cap(u64 slot, u32 kind, u32 right) {
    return slot < 16 && kinds[slot] == kind && (rights[slot] & right) == right;
}
u64 sched_cap_obj(u64 slot) { assert(slot < 16); return objects[slot]; }
void sched_yield(void) {}
void sched_sleep(u64 n) { (void)n; }
void sched_exit(u64 n) { (void)n; abort(); }
u64 ipc_send(u64 ep, u64 src, u64 n) { (void)ep; (void)src; (void)n; ++send_calls; return 0; }
u64 ipc_recv(u64 ep, u64 dst, u64 n) { (void)ep; (void)dst; ++recv_calls; return n; }
u64 proc_map_module(u64 n) { (void)n; return 0; }
u64 proc_module_size(u64 n) { (void)n; return 0; }
u64 proc_map_anon(u64 id, u64 n) { (void)id; (void)n; return 0; }
static u8 test_scancode;
u8 inb(u16 port) { return port == 0x60 ? test_scancode : (port==0x64?1:0); }
void pointer_byte(u8 c) { (void)c; }
u64 pointer_get(void) { return 0; }
void desktop_begin(void) {}
void desktop_end(void) {}
u64 workspace_geometry(void) { return ((u64)800<<32)|1280; }
u64 workspace_hit(u64 x,u64 y) { (void)x;(void)y;return 0; }
bool workspace_view(u8 *p,u64 len) { (void)p;(void)len;return true; }
u64 storage_size(void) { return 262144; }
bool storage_io(u64 op,u64 sector,u8 *p) { (void)p;return op==3 || sector<262144; }
static void key(u8 scan) { test_scancode = scan; keyboard_irq(); }

static void test_cancel(void) {
    const u64 cap_error = UINT64_MAX, arg_error = UINT64_MAX - 1;
    kinds[4] = 5;
    key(30); // a
    key(1); // Escape, kept outside ordinary typeahead
    key(48); // b
    assert(syscall_dispatch(17, 0, 0, 0) == cap_error);
    assert(syscall_dispatch(17, 4, 2, 0) == arg_error);
    assert(syscall_dispatch(17, 4, 0, 1) == arg_error);
    assert(syscall_dispatch(17, 4, 0, 0) == 1);
    assert(syscall_dispatch(17, 4, 0, 0) == 1); // sticky during every layer
    assert(syscall_dispatch(17, 4, 1, 0) == 1);
    assert(syscall_dispatch(17, 4, 0, 0) == 0);
    assert(keyboard_getc() == 'a');
    assert(keyboard_getc() == 'b');
    assert(keyboard_getc() == 0);
    for (unsigned i = 0; i < 260; ++i) key(30);
    key(1); // Escape still works with a full queue
    assert(syscall_dispatch(17, 4, 1, 0) == 1);
    unsigned count = 0;
    while (keyboard_getc()) ++count;
    assert(count == 255);
    key(1);
    assert(keyboard_getc() == 27); // idle prompt consumes the escape
    assert(!keyboard_cancelled(false));
}

static void test_shift_enter(void) {
    keyboard_init();
    key(0x1c); key(0x9c); // press/release plain Enter
    assert(keyboard_getc() == 10 && keyboard_getc() == 0);
    key(0x2a); key(0x1c); key(0x9c); key(0xaa);
    assert(keyboard_getc() == 11 && keyboard_getc() == 0);
    key(0x36); key(0x1c); key(0x9c); key(0xb6);
    assert(keyboard_getc() == 11 && keyboard_getc() == 0);
    // Releasing one Shift while the other is down must keep the modifier.
    key(0x2a); key(0x36); key(0xaa); key(0x1c); key(0x9c); key(0xb6);
    key(0x1c); key(0x9c);
    assert(keyboard_getc() == 11 && keyboard_getc() == 10 && keyboard_getc() == 0);
    // Extended fake Shift (PrintScreen) cannot alter the real key state.
    key(0x2a); key(0xe0); key(0xaa); key(0x1c); key(0xaa);
    assert(keyboard_getc() == 11);
    key(0xe0); key(0x2a); key(0x1c);
    assert(keyboard_getc() == 10);
    key(0x2a); key(1); key(0xaa);
    assert(keyboard_getc() == 27 && !keyboard_cancelled(false));
    puts("OK: Enter, both Shift keys, keyup, PrintScreen and Escape");
}

static void setup_pages(void) {
    memset(pml4, 0, sizeof(pml4)); memset(pdpt, 0, sizeof(pdpt));
    memset(pd, 0, sizeof(pd)); memset(pt, 0, sizeof(pt));
    pml4[0] = (u64)pdpt | 7; pdpt[0] = (u64)pd | 7; pd[2] = (u64)pt | 7;
    pt[0] = 0x100000 | 7; // rw user at 0x400000
    pt[1] = 0x101000 | 5; // read-only user at 0x401000
}

static void test_pages(void) {
    setup_pages();
    assert(vmm_user_range_ok((u64)pml4, 0x400000, 4096, true));
    assert(vmm_user_range_ok((u64)pml4, 0x400fff, 2, false));
    assert(!vmm_user_range_ok((u64)pml4, 0x400fff, 2, true));
    assert(!vmm_user_range_ok((u64)pml4, 0x401fff, 2, false));
    assert(!vmm_user_range_ok((u64)pml4, 0, 1, false));
    assert(!vmm_user_range_ok((u64)pml4, 0x400000, UINT64_MAX, false));
    assert(!vmm_user_range_ok((u64)pml4, 0x7fffffffffff, 2, false));
    assert(!vmm_user_range_ok((u64)pml4, 0x800000000000, 0, false));
    pml4[0] &= ~4ULL;
    assert(!vmm_user_range_ok((u64)pml4, 0x400000, 1, false));
    setup_pages(); pdpt[0] &= ~2ULL;
    assert(!vmm_user_range_ok((u64)pml4, 0x400000, 1, true));
    assert(vmm_user_range_ok((u64)pml4, 0x400000, 1, false));
    setup_pages(); pd[2] &= ~4ULL;
    assert(!vmm_user_range_ok((u64)pml4, 0x400000, 1, false));
    setup_pages(); pd[2] = 0x200000 | 0x87;
    assert(vmm_user_range_ok((u64)pml4, 0x400000, 4096, true));
    pdpt[0] = 0x40000000 | 0x85;
    assert(vmm_user_range_ok((u64)pml4, 0x400000, 4096, false));
    assert(!vmm_user_range_ok((u64)pml4, 0x400000, 4096, true));
    setup_pages();
}

static void test_api(void) {
    const u64 cap_error = UINT64_MAX, arg_error = UINT64_MAX - 1;
    kinds[0] = 1; kinds[3] = 4; kinds[7] = 8;
    rights[7] = 1; // read-only system authority
    kinds[8] = 3; rights[8] = 3;
    assert(syscall_dispatch(14, 7, 0, 0) == 42000);
    assert(syscall_dispatch(14, 7, 1, 0) == 1234);
    assert(syscall_dispatch(14, 7, 2, 0) == 2048);
    assert(syscall_dispatch(14, 7, 3, 0) == 3);
    assert(syscall_dispatch(14, 7, 4, 0) == 77);
    assert(syscall_dispatch(14, 7, 8, 0) == arg_error);
    assert(syscall_dispatch(14, 7, 0, 1) == arg_error);
    assert(syscall_dispatch(15, 7, 1, 0) == cap_error);
    assert(syscall_dispatch(14, 0, 0, 0) == cap_error);
    assert(syscall_dispatch(14, UINT64_MAX, 0, 0) == cap_error);
    assert(syscall_dispatch(16, 3, 1, 0) == cap_error);
    assert(syscall_dispatch(13, 3, 3, 0) == cap_error);
    assert(syscall_dispatch(13, 3, 2, 0) == 0);
    assert(syscall_dispatch(14, 7, 5, 0) == 5);
    // Keep the original five-denial check before the newly denied halo states.
    assert(syscall_dispatch(13, 3, 4, 0) == cap_error);
    assert(syscall_dispatch(13, 3, 5, 0) == cap_error);
    assert(syscall_dispatch(13, 3, 6, 0) == arg_error);
    objects[7] = 1;
    assert(syscall_dispatch(14, 7, 0, 0) == cap_error);
    objects[7] = 0; rights[7] = 3; rights[3] = 1;
    assert(syscall_dispatch(13, 3, 4, 0) == 0);
    assert(syscall_dispatch(13, 3, 5, 0) == cap_error);
    rights[3] = 3;
    assert(syscall_dispatch(13, 3, 5, 0) == 0);
    rights[3] = 1;
    assert(syscall_dispatch(15, 7, 2, 0) == arg_error);
    assert(syscall_dispatch(15, 7, 1, 1) == arg_error);
    assert(syscall_dispatch(14, 7, 6, 0) == 0);
    assert(syscall_dispatch(14, 7, 7, 0) == 0);
    assert(syscall_dispatch(15, 7, 1, 0) == 1);
    assert(syscall_dispatch(15, 7, 1, 0) == 1);
    assert(syscall_dispatch(14, 7, 6, 0) == 1);
    assert(syscall_dispatch(15, 7, 0, 0) == 2);
    assert(syscall_dispatch(16, 3, 0, 0) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 1) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 0) == 0);
    assert(syscall_dispatch(16, 3, 1, 5) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 16 * 7 + 5) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 16 * 7 + 0) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 16 * 7 + 2) == 0);
    assert(syscall_dispatch(16, 3, 1, 16 * 7 + 2) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 16 * 6 + 3) == arg_error);
    assert(syscall_dispatch(16, 3, 2, 16 * 9 + 1) == arg_error);
    assert(syscall_dispatch(16, 3, 1, 0) == 0);
    assert(syscall_dispatch(16, 3, 1, 16 * 9 + 1) == 0);
    assert(syscall_dispatch(13, 3, 3, 0) == 0);
    assert(syscall_dispatch(1, 0, 0x20000000, 1) == arg_error);
    assert(syscall_dispatch(8, 3, 0x20000000, 1) == arg_error);
    assert(syscall_dispatch(5, 8, 0x401fff, 2) == arg_error);
    assert(syscall_dispatch(6, 8, 0x401000, 1) == arg_error);
    assert(syscall_dispatch(6, 8, 0x400fff, 2) == arg_error);
    assert(syscall_dispatch(5, 8, 0x400000, 257) == arg_error);
    assert(syscall_dispatch(6, 8, 0x400000, UINT64_MAX) == arg_error);
    assert(recv_calls == 0 && send_calls == 0);
    assert(syscall_dispatch(5, 8, 0x401000, 1) == 0); // source may be read-only
    assert(syscall_dispatch(6, 8, 0x400000, 1) == 1);
    assert(recv_calls == 1 && send_calls == 1);
}

static void test_compositor(void) {
    const size_t width = 1024, height = 768;
    u32 *pixels = calloc(width * height, sizeof(*pixels));
    u32 *snapshot = calloc(width * height, sizeof(*snapshot));
    assert(pixels && snapshot);
    struct Fb fb = { pixels, width, height, width * 4, 16, 8, 0 };
    console_init(&fb);
    system_present();
    memcpy(snapshot, pixels, width * height * 4);
    const u8 line[] = "text cannot overwrite verified status or trusted halo\n";
    for (int n = 0; n < 200; ++n) console_write((u8 *)line, sizeof(line) - 1);
    for (size_t y = 72; y < height; ++y)
        assert(memcmp(pixels + y * width + 704, snapshot + y * width + 704, 320 * 4) == 0);
    assert(memcmp(pixels, snapshot, width * 72 * 4) == 0);
    // Only the accepted SYS_HALO route refreshes the shown panel.
    memcpy(snapshot, pixels, width * height * 4);
    kinds[3] = 4; rights[3] = 0;
    assert(syscall_dispatch(13, 3, 2, 0) == 0);
    bool halo_row_changed = false;
    for (size_t y = 72 + 24 + 20 * 27; y < 72 + 24 + 20 * 27 + 16; ++y)
        halo_row_changed |= memcmp(pixels + y * width + width - 320 + 20,
                                  snapshot + y * width + width - 320 + 20,
                                  (320 - 20 - 12) * 4) != 0;
    assert(halo_row_changed);
    for (size_t y = 72; y < height; ++y)
        assert(memcmp(pixels + y * width, snapshot + y * width, (width - 320) * 4) == 0);
    memcpy(snapshot, pixels, width * height * 4);
    console_set_halo(0);
    for (size_t y = 72; y < height; ++y)
        assert(memcmp(pixels + y * width + width - 320,
                      snapshot + y * width + width - 320, 320 * 4) == 0);
    rights[3] = 1;
    const u32 normal_border = pixels[72 * width + 704];
    system_set_mode(1);
    assert(pixels[72 * width + 704] != normal_border);
    const u64 revision = system_inspect(7);
    memcpy(snapshot, pixels, width * height * 4);
    assert(system_set_mode(1) == revision);
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    system_set_mode(0);
    assert(pixels[72 * width + 704] == normal_border);
    // Whole and token-split UTF-8 render identically; Latin-1 uses one glyph.
    const u8 spanish[] = "Espa\xc3\xb1ol: \xc2\xbfQu\xc3\xa9 tal?";
    console_init(&fb);
    console_write((u8 *)spanish, sizeof(spanish) - 1);
    memcpy(snapshot, pixels, width * height * 4);
    console_init(&fb);
    for (size_t i = 0; i < sizeof(spanish) - 1; ++i) console_write((u8 *)spanish + i, 1);
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    console_init(&fb);
    u8 enye[] = {0xc3, 0xb1};
    console_write(enye, 2);
    memcpy(snapshot, pixels, width * height * 4);
    console_init(&fb);
    console_putc(0xf1);
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    bool different = false;
    for (u64 i = 0; i < 16; ++i) different |= font_row(0xf1, i) != font_row('?', i);
    assert(different);
    // Removing a newline and a wrapped suffix repaints exactly the surviving
    // input; previous text, the verified panel and halo remain intact.
    const char *before = "historial\n\x0e> abc\n    error";
    const char *repaint = "\x0f> abc";
    const char *expected = "historial\n\x0e> abc";
    console_init(&fb); system_present();
    console_write((u8 *)before, strlen(before));
    console_write((u8 *)repaint, strlen(repaint));
    memcpy(snapshot, pixels, width * height * 4);
    console_init(&fb); system_present();
    console_write((u8 *)expected, strlen(expected));
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    char long_input[201]; memset(long_input, 'x', 200); long_input[200] = 0;
    console_init(&fb); system_present();
    console_write((u8 *)"historial\n\x0e> ", 13);
    console_write((u8 *)long_input, 200); console_write((u8 *)"\nerror", 6);
    console_write((u8 *)"\x0f> ", 3); console_write((u8 *)long_input, 200);
    memcpy(snapshot, pixels, width * height * 4);
    console_init(&fb); system_present();
    console_write((u8 *)"historial\n\x0e> ", 13); console_write((u8 *)long_input, 200);
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    // All six trusted headers differ, with both other surfaces untouched.
    console_init(&fb); system_present();
    for (int n = 0; n < 3; ++n) console_write((u8 *)line, sizeof(line) - 1);
    memcpy(snapshot, pixels, width * height * 4);
    u32 *headers = calloc(6 * width * 72, sizeof(*headers));
    assert(headers);
    for (u64 s = 0; s < 6; ++s) {
        console_set_halo(s);
        memcpy(headers + s * width * 72, pixels, width * 72 * 4);
        for (size_t y = 72; y < height; ++y) {
            assert(memcmp(pixels + y * width, snapshot + y * width, (width - 320) * 4) == 0);
            assert(memcmp(pixels + y * width + width - 320,
                          snapshot + y * width + width - 320, 320 * 4) == 0);
        }
    }
    for (size_t i = 0; i < 6; ++i)
        for (size_t j = i + 1; j < 6; ++j)
            assert(memcmp(headers + i * width * 72, headers + j * width * 72, width * 72 * 4) != 0);
    memcpy(snapshot, pixels, width * height * 4);
    console_set_halo(6);
    assert(memcmp(pixels, snapshot, width * height * 4) == 0);
    console_set_halo(0);
    free(headers);
    puts("OK: UI-01 six distinct headers; text/panel isolation; halo transition refresh");
    free(snapshot); free(pixels);
}

int main(void) {
    test_pages(); test_api(); test_cancel(); test_shift_enter(); test_compositor();
    // A model console capability cannot draw documents; buffer checks precede reads.
    const u64 cap_error=UINT64_MAX, arg_error=UINT64_MAX-1;
    assert(syscall_dispatch(21,4,0,0)==cap_error);
    kinds[12]=10; rights[12]=1;
    assert(syscall_dispatch(21,12,0,0)==262144);
    assert(syscall_dispatch(21,12,0,3)==cap_error);
    assert(syscall_dispatch(21,12,0xdeadbeef,33)==arg_error);
    rights[12]=3;
    assert(syscall_dispatch(21,12,0,3)==0);
    assert(syscall_dispatch(21,12,1,3)==arg_error);
    assert(syscall_dispatch(21,12,0,7)==arg_error);
    objects[12]=1;assert(syscall_dispatch(21,12,0,0)==cap_error);objects[12]=0;
    assert(syscall_dispatch(22,0,0,0)==cap_error);
    assert(syscall_dispatch(22,4,3,0)==arg_error);
    assert(syscall_dispatch(22,4,0,1)==arg_error);
    assert(syscall_dispatch(23,4,0,0)==cap_error);
    kinds[3]=4;rights[3]=0;assert(syscall_dispatch(23,3,0,0)==cap_error);
    rights[3]=1;assert(syscall_dispatch(23,3,0xdeadbeef,448)==arg_error);
    assert(syscall_dispatch(23,3,0x400000,2049)==arg_error);
    puts("OK: D1 storage/pointer/workspace capabilities, rights and bounded buffers");
    kinds[3]=4; rights[3]=0;
    assert(syscall_dispatch(18,3,0x400000,128)==cap_error);
    rights[3]=1;
    assert(syscall_dispatch(18,3,0,128)==arg_error);
    assert(syscall_dispatch(18,3,0x400000,32769)==arg_error);
    assert(syscall_dispatch(18,3,1,0)==arg_error);
    assert(syscall_dispatch(18,3,0x20000000,128)==arg_error);
    puts("OK: native document capability and user buffer rejection");
    puts("OK: M4 capabilities, revisions, buffers, Escape and UTF-8 compositor");
    return 0;
}
