/* ErlanOS L1 — entrada del kernel (M4).
 * C sólo como pegamento con Limine; la lógica de L1 vive en los módulos .e.
 * Nada de IA aquí: el modelo corre en un servicio de L2 (services/model). */
#include <stdint.h>
#include <stddef.h>
#include "limine.h"
#include "serial.h"
#include "panic.h"
#include "cpu.h"
#include "fb.h"
#include "gdt.h"
#include "idt.h"
#include "pic.h"
#include "pit.h"
#include "keyboard.h"
#include "pmm.h"
#include "heap.h"
#include "fpu.h"
#include "vmm.h"
#include "sched.h"
#include "ipc.h"
#include "proc.h"
#include "console.h"
#include "pci.h"
#include "hardware.h"
#include "storage.h"
#include "pointer.h"
#include "workspace_view.h"
#ifdef ERLANOS_NET_PROBE
#include "e1000.h"
#include "netmain.h"
#endif

LIMINE_REQUESTS_START_MARKER
LIMINE_BASE_REVISION(3)

LIMINE_REQUEST struct limine_bootloader_info_request info_req = {
    .id = LIMINE_BOOTLOADER_INFO_REQUEST, .revision = 0, .response = NULL };
LIMINE_REQUEST struct limine_framebuffer_request fb_req = {
    .id = LIMINE_FRAMEBUFFER_REQUEST, .revision = 0, .response = NULL };
LIMINE_REQUEST struct limine_hhdm_request hhdm_req = {
    .id = LIMINE_HHDM_REQUEST, .revision = 0, .response = NULL };
LIMINE_REQUEST struct limine_memmap_request memmap_req = {
    .id = LIMINE_MEMMAP_REQUEST, .revision = 0, .response = NULL };
LIMINE_REQUEST struct limine_module_request module_req = {
    .id = LIMINE_MODULE_REQUEST, .revision = 0, .response = NULL,
    .internal_module_count = 0, .internal_modules = NULL };

LIMINE_REQUESTS_END_MARKER

/* Tipos de capability y derechos (deben coincidir con sched.e / syscall.e). */
#define CAP_LOG      1
#define CAP_TIME     2
#define CAP_ENDPOINT 3
#define CAP_CONSOLE  4
#define CAP_INPUT    5
#define CAP_MODULE   6
#define CAP_MEMORY   7
#define CAP_SYSTEM   8
#define CAP_STORAGE  10
#define RIGHT_SEND   1
#define RIGHT_RECV   2
#define RIGHT_PRESENT 1
#define RIGHT_EGRESS  2 /* nunca se concede */

#define MAX_REGIONS 128
static struct MemRegion regions[MAX_REGIONS];

static const char *memmap_type_name(uint64_t t) {
    switch (t) {
        case LIMINE_MEMMAP_USABLE:                 return "usable";
        case LIMINE_MEMMAP_RESERVED:               return "reserved";
        case LIMINE_MEMMAP_ACPI_RECLAIMABLE:       return "acpi-reclaim";
        case LIMINE_MEMMAP_ACPI_NVS:               return "acpi-nvs";
        case LIMINE_MEMMAP_BAD_MEMORY:             return "bad";
        case LIMINE_MEMMAP_BOOTLOADER_RECLAIMABLE: return "bootloader-reclaim";
        case LIMINE_MEMMAP_EXECUTABLE_AND_MODULES: return "kernel+modules";
        case LIMINE_MEMMAP_FRAMEBUFFER:            return "framebuffer";
        default:                                   return "?";
    }
}

/* "/boot/init.bin" -> "init" y extensión "bin" (buffers estáticos por módulo) */
static char module_names[8][32];
static char module_exts[8][8];
static const char *module_short_name(uint64_t i, const char *path) {
    const char *base = path;
    for (const char *p = path; *p; p++) if (*p == '/') base = p + 1;
    const char *dot = NULL;
    for (const char *p = base; *p; p++) if (*p == '.') dot = p;
    uint64_t n = 0;
    while (base[n] && base + n != dot && n < 31) { module_names[i][n] = base[n]; n++; }
    module_names[i][n] = 0;
    uint64_t e = 0;
    if (dot) { dot++; while (*dot && e < 7) module_exts[i][e++] = *dot++; }
    module_exts[i][e] = 0;
    return module_names[i];
}

static int str_eq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static void init_console(void) {
    if (!fb_req.response || fb_req.response->framebuffer_count < 1) kpanic("sin framebuffer");
    struct limine_framebuffer *lf = fb_req.response->framebuffers[0];
    /* The renderer writes u32 pixels. Reject other firmware modes before use. */
    if (!lf || !lf->address || lf->bpp != 32 || lf->memory_model != 1 ||
        !lf->width || !lf->height || lf->width > UINT64_MAX / 4 ||
        lf->pitch < lf->width * 4 || lf->red_mask_shift > 24 ||
        lf->green_mask_shift > 24 || lf->blue_mask_shift > 24)
        kpanic("framebuffer: modo RGB32 requerido");
    kprintf("framebuffer: %dx%d bpp=%d pitch=%d\n",
            lf->width, lf->height, (uint64_t)lf->bpp, lf->pitch);
    static struct Fb f;
    fb_init(&f, (u32 *)lf->address, lf->width, lf->height, lf->pitch,
            lf->red_mask_shift, lf->green_mask_shift, lf->blue_mask_shift);
    console_init(&f);
}

void kmain(void) {
    serial_init();
    kprintf("\nErlanOS L1 (M4) — kernel boot\n");

    if (!LIMINE_BASE_REVISION_SUPPORTED) kpanic("limine: base revision no soportada");
    if (info_req.response)
        kprintf("bootloader: %s %s\n", info_req.response->name, info_req.response->version);
    if (!hhdm_req.response || !memmap_req.response) kpanic("limine: sin hhdm o memmap");

    uint64_t hhdm = hhdm_req.response->offset;
    kprintf("hhdm offset: %x\n", hhdm);

    struct limine_memmap_response *mm = memmap_req.response;
    uint64_t n = 0;
    for (uint64_t i = 0; i < mm->entry_count && n < MAX_REGIONS; i++) {
        struct limine_memmap_entry *e = mm->entries[i];
        kprintf("  %x  +%x  %s\n", e->base, e->length, memmap_type_name(e->type));
        regions[n].base = e->base;
        regions[n].len = e->length;
        regions[n].usable = (e->type == LIMINE_MEMMAP_USABLE);
        n++;
    }

#ifdef ERLANOS_HARDWARE_PROBE
    /* A separate diagnostic image: no service loading, allocator or DMA setup. */
    cpu_cli();
    gdt_init();
    idt_init();
    init_console();
    pci_init();
    hardware_report();
    kprintf("[hardware] probe: ready\n");
    for (;;) cpu_hlt();
#endif

    /* --- M1: base de máquina --- */
    gdt_init();
    idt_init();
    pci_init();
    pic_init();
    pit_init(100);
    keyboard_init();
    pmm_init((erlan_sSMemRegion){ regions, n }, hhdm);
    heap_init();
    heap_selftest();
    fpu_init();

    /* --- M2: memoria virtual, hilos, IPC --- */
    vmm_init();
    sched_init();
    ipc_init();
    proc_init();

#ifdef ERLANOS_NET_PROBE
    /* Imagen de prueba de red: mismo arranque que el kernel normal, sin servicios.
     * El driver e1000 (L1) y la pila net/ (Erlan) demuestran DHCP y DNS reales. */
    init_console();
    kprintf("[net] probe: inicio\n");
    if (!e1000_init()) {
        console_write((u8 *)"Sin NIC e1000 compatible: la prueba de red no puede continuar.\n", 63);
        kprintf("[net] FALLO: sin NIC e1000 compatible\n");
        kprintf("[net] probe: ready\n");
        for (;;) cpu_hlt();
    }
    cpu_sti();
    net_probe_run();
    kprintf("[net] probe: ready\n");
    for (;;) cpu_hlt();
#endif

    /* Pantalla: puntos + consola de texto */
    init_console();
    storage_init();
    pointer_init();
    desktop_end();

    /* Módulos de Limine: .bin = servicio de usuario (L2); .gguf = modelo (datos).
     * Las capabilities se conceden aquí, explícitamente, por proceso. */
    uint64_t ep = ipc_create();
    uint64_t ai_requests = ipc_create();
    uint64_t ai_replies = ipc_create();
    uint64_t data_idx = 0;
    if (module_req.response) {
        struct limine_module_response *mr = module_req.response;
        kprintf("modulos: %d\n", mr->module_count);
        for (uint64_t i = 0; i < mr->module_count && i < 8; i++) {
            struct limine_file *m = mr->modules[i];
            const char *name = module_short_name(i, m->path);
            kprintf("  %s  (%d bytes)\n", m->path, m->size);
            if (str_eq(module_exts[i], "gguf")) {
                proc_register_module(data_idx++, name, (uint64_t)(uintptr_t)m->address, m->size);
                continue;
            }
            if (!str_eq(module_exts[i], "bin")) continue;
            uint64_t id = proc_spawn(name, (uint64_t)(uintptr_t)m->address, m->size);
            if (!id) kpanic("proc: imagen de servicio invalida o sin memoria");
            sched_grant(id, 0, CAP_LOG, 0, 0);
            sched_grant(id, 1, CAP_TIME, 0, 0);
            if (str_eq(name, "init")) {
                sched_grant(id, 2, CAP_ENDPOINT, RIGHT_SEND, ep);
                sched_grant(id, 7, CAP_SYSTEM, 1, 0); /* read only, tested in init */
            }
            if (str_eq(name, "echo")) sched_grant(id, 2, CAP_ENDPOINT, RIGHT_RECV, ep);
            if (str_eq(name, "model")) {
                sched_grant(id, 3, CAP_CONSOLE, 0, 0);
                sched_grant(id, 4, CAP_INPUT, 0, 0);
                sched_grant(id, 5, CAP_MODULE, 0, 0);
                sched_grant(id, 6, CAP_MEMORY, 0, 0);
                sched_grant(id, 8, CAP_ENDPOINT, RIGHT_SEND, ai_requests);
                sched_grant(id, 9, CAP_ENDPOINT, RIGHT_RECV, ai_replies);
            }
            if (str_eq(name, "system")) {
                sched_grant(id, 12, CAP_STORAGE, 3, 0);
                sched_grant(id, 3, CAP_CONSOLE, 1, 0); /* structured presentation */
                sched_grant(id, 7, CAP_SYSTEM, 3, 0);  /* read + set mode */
                sched_grant(id, 8, CAP_ENDPOINT, RIGHT_RECV, ai_requests);
                sched_grant(id, 9, CAP_ENDPOINT, RIGHT_SEND, ai_replies);
            }
        }
    }

    kprintf("estado: idle  (• • •)  cediendo el CPU a los servicios\n");
    sched_start();
}
