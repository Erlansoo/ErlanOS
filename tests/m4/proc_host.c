/* Actual proc_map_anon with a counting mapper: rejected allocations must neither
 * map pages nor consume virtual address space, including wraparound after use. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "proc.h"

static u64 available = 1000000, mapped_calls, mapped_pages, last_base;
static u64 module_maps, module_first, module_last, module_phys;
static u64 spaces, spawn_code_pages, spawn_stack_pages;
static bool spawn_mode;
static u8 copied_image[262144], source_image[131073];
void erlan_panic(const char *msg) { fprintf(stderr, "%s\n", msg); abort(); }
void kprintf(const char *fmt, ...) { (void)fmt; }
u64 pmm_phys_to_virt(u64 n) { return n; }
u64 pmm_virt_to_phys(u64 n) { return n; }
u64 pmm_free_count(void) { return available; }
u64 sched_current_cr3(void) { return 0x1000; }
u64 sched_create(const char *name, u64 rip, u64 rsp, u64 cr3, bool user) {
    (void)name; (void)rip; (void)rsp; (void)cr3; (void)user; return 1;
}
u64 vmm_new_space(void) { ++spaces; return 0x1000; }
void vmm_map(u64 cr3, u64 virt, u64 phys, u64 flags) {
    assert(cr3 == 0x1000 && flags == 4);
    if (!module_maps) { module_first = virt; module_phys = phys; }
    else { assert(virt == module_last + 4096); assert(phys == module_phys + module_maps * 4096); }
    module_last = virt; ++module_maps;
}
u64 vmm_translate(u64 cr3, u64 virt) {
    assert(spawn_mode && cr3 == 0x1000 && virt >= 0x400000);
    assert(virt - 0x400000 < sizeof copied_image);
    return (u64)(uintptr_t)(copied_image + virt - 0x400000);
}
u64 vmm_map_new(u64 cr3, u64 virt, u64 pages, u64 flags) {
    assert(cr3 == 0x1000 && flags == 6);
    assert(pages > 0 && pages <= 65536 && pages <= available);
    if (spawn_mode) {
        if (virt == 0x400000) { spawn_code_pages = pages; memset(copied_image, 0, sizeof copied_image); }
        else { assert(virt == 0xFF0000); spawn_stack_pages = pages; }
        return 0x2000;
    }
    ++mapped_calls; mapped_pages += pages; last_base = virt;
    return 0x2000;
}

int main(void) {
    const u64 base = 0x20000000;
    proc_init();
    assert(proc_map_anon(1, 2) == base);
    assert(mapped_calls == 1 && mapped_pages == 2 && last_base == base);
    // Original used + pages check wraps 2 + UINT64_MAX to 1 and allowed mapping.
    assert(proc_map_anon(1, UINT64_MAX) == 0);
    assert(proc_map_anon(1, UINT64_MAX - 1) == 0);
    assert(proc_map_anon(1, 65535) == 0);
    assert(mapped_calls == 1 && mapped_pages == 2);
    assert(proc_map_anon(1, 1) == base + 2 * 4096);
    assert(mapped_calls == 2 && mapped_pages == 3);
    available = 1; // data would fit, but page tables have no memory budget
    assert(proc_map_anon(1, 1) == 0);
    available = 100; // not enough data pages for this allocation
    assert(proc_map_anon(1, 512) == 0);
    assert(mapped_calls == 2 && mapped_pages == 3);
    available = 1000000;
    assert(proc_map_anon(1, 1) == base + 3 * 4096);
    assert(proc_map_anon(1, 65532) == base + 4 * 4096); // exact virtual capacity
    assert(mapped_calls == 4 && mapped_pages == 65536);
    assert(proc_map_anon(1, 1) == 0);
    assert(proc_map_anon(1, UINT64_MAX) == 0);
    assert(proc_map_anon(1, 0) == 0);
    assert(proc_map_anon(32, 1) == 0);
    assert(mapped_calls == 4 && mapped_pages == 65536);
    assert(proc_map_anon(2, 1) == base); // limits remain per process
    puts("OK: anonymous memory overflow, physical budget and unchanged state on rejection");

    // Real mapper path with a >2 GiB resident model; do not allocate its bytes.
    const u64 model_bytes = 0x80000000ULL + 123;
    const u64 model_phys = 0x100000123ULL; // nonaligned and above 4 GiB
    const u64 module_base = 0x40000000ULL, stride = 0x200000000ULL;
    const u64 page_count = (0x123 + model_bytes + 4095) / 4096;
    proc_register_module(0, "qwen3.5", model_phys, model_bytes);
    assert(proc_module_size(0) == model_bytes);
    available = (page_count + 511) / 512 + (page_count + 262143) / 262144;
    assert(proc_map_module(0) == 0 && module_maps == 0); // lacks PDPT budget
    available++;
    assert(proc_map_module(0) == module_base + 0x123);
    assert(module_maps == page_count && module_first == module_base);
    assert(module_phys == (model_phys & ~4095ULL));
    const u64 prior_end = module_last + 4096;
    module_maps = 0;
    proc_register_module(1, "coder", 0x300000000ULL, 4096);
    assert(proc_map_module(1) == module_base + stride);
    assert(module_first >= prior_end && module_maps == 1);
    module_maps = 0;
    proc_register_module(2, "oversized", 0x123, stride);
    assert(proc_module_size(2) == 0 && proc_map_module(2) == 0);
    proc_register_module(2, "wrap", 0x123, UINT64_MAX);
    assert(proc_module_size(2) == 0 && proc_map_module(2) == 0);
    proc_register_module(2, "physical-end", 0x000FFFFFFFFFF000ULL, 4097);
    assert(proc_module_size(2) == 0 && proc_map_module(2) == 0);
    proc_register_module(2, "empty", 0x1000, 0);
    assert(proc_map_module(2) == 0 && proc_map_module(8) == 0 && module_maps == 0);
    assert(proc_module_pages(0x1000, stride) == stride / 4096); // exact slot bound
    puts("OK: >2 GiB modules, >4 GiB physical addresses, isolated 8 GiB slots and atomic budget rejection");

    available = 1;
    assert(proc_spawn("low-memory", (u64)(uintptr_t)source_image, sizeof source_image) == 0);
    assert(spaces == 0);
    available = 1000000;
    assert(proc_spawn("empty", 1, 0) == 0);
    assert(proc_spawn("too-large", 1, 0xBF0001) == 0);
    assert(proc_spawn("wrap", UINT64_MAX - 5, 16) == 0 && spaces == 0);
    spawn_mode = true;
    source_image[0] = 0x90; source_image[sizeof source_image - 1] = 0x5A;
    assert(proc_spawn("large-bss", (u64)(uintptr_t)source_image, sizeof source_image) == 1);
    assert(spawn_code_pages == (sizeof source_image + 4095) / 4096);
    assert(spawn_stack_pages == 16 && spaces == 1);
    assert(!memcmp(source_image, copied_image, sizeof source_image));
    for (size_t i = sizeof source_image; i < spawn_code_pages * 4096; ++i) assert(!copied_image[i]);
    puts("OK: flat image uses actual byte length, zero page tail, and rejects exhausted/colliding images before allocation");
    return 0;
}
