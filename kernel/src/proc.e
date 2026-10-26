// L1 — procesos de usuario: carga de binarios planos (módulos de Limine) en un espacio
// de direcciones propio, registro de módulos de datos (p. ej. el modelo GGUF) y mapeo
// de memoria bajo demanda para los servicios.

import vmm;
import pmm;
import kmem;
import sched;
import serial;

const USER_BASE: u64 = 0x400000;
const USER_STACK_TOP: u64 = 0x1000000;   // 16 MiB
const USER_STACK_PAGES: u64 = 16;         // 64 KiB
const PTE_W: u64 = 2;
const PTE_U: u64 = 4;

const MAX_MODULES: u64 = 8;
const MODULE_MAP_BASE: u64 = 0x40000000;  // 1 GiB: módulos de datos mapeados sólo lectura
const MODULE_MAP_STRIDE: u64 = 0x200000000; // 8 GiB por módulo, sin solapar modelos grandes
const MAX_PHYS_ADDR: u64 = 0x000FFFFFFFFFFFFF;
const ANON_BASE: u64 = 0x20000000;        // 512 MiB: memoria anónima pedida por el servicio
const ANON_LIMIT_PAGES: u64 = 65536;      // 256 MiB por proceso

struct DataModule {
    used: bool;
    addr: u64;    // dirección virtual del kernel (HHDM)
    size: u64;
    name: str;
}

var modules: [8]DataModule;
var anon_next: [32]u64;    // siguiente dirección anónima libre por hilo (id)

fn proc_init() {
    for i in 0..MAX_MODULES { modules[i].used = false; }
    for i in 0..32 { anon_next[i] = ANON_BASE; }
}

// Registra un módulo de datos (no ejecutable) para que los servicios lo mapeen.
fn proc_register_module(idx: u64, name: str, addr: u64, size: u64) {
    if idx >= MAX_MODULES { return; }
    modules[idx].used = false;
    if proc_module_pages(addr, size) == 0 {
        kprintf("[proc] modulo %d rechazado: rango o tamano invalido\n", idx);
        return;
    }
    modules[idx].used = true;
    modules[idx].addr = addr;
    modules[idx].size = size;
    modules[idx].name = name;
    kprintf("[proc] modulo de datos %d '%s': %d bytes\n", idx, name, size);
}

// Validate the full physical span and virtual slot before arithmetic or maps.
// Limine supplies resident module bytes; mapping them needs page tables only.
fn proc_module_pages(addr: u64, size: u64) -> u64 {
    if size == 0 { return 0; }
    let phys = pmm_virt_to_phys(addr);
    if phys > MAX_PHYS_ADDR || size - 1 > MAX_PHYS_ADDR - phys { return 0; }
    let off = phys & 0xFFF;
    if size > MODULE_MAP_STRIDE - off { return 0; }
    return (off + size + 4095) / 4096;
}

fn proc_module_size(idx: u64) -> u64 {
    if idx >= MAX_MODULES || !modules[idx].used { return 0; }
    return modules[idx].size;
}

// Mapea el módulo idx en el espacio del hilo actual, sólo lectura. Devuelve la virtual.
fn proc_map_module(idx: u64) -> u64 {
    if idx >= MAX_MODULES || !modules[idx].used { return 0; }
    let cr3 = sched_current_cr3();
    let phys0 = pmm_virt_to_phys(modules[idx].addr);
    let off = phys0 & 0xFFF;
    let pages = proc_module_pages(modules[idx].addr, modules[idx].size);
    if pages == 0 { return 0; }
    // Slots start on a GiB boundary and remain in one PML4 entry. Bound PT,
    // PD and PDPT allocations; syscall execution cannot be preempted here.
    let tables = (pages + 511) / 512 + (pages + 262143) / 262144 + 1;
    if tables > pmm_free_count() { return 0; }
    let base = MODULE_MAP_BASE + idx * MODULE_MAP_STRIDE;
    for p in 0..pages {
        vmm_map(cr3, base + p * 4096, (phys0 - off) + p * 4096, PTE_U);
    }
    kprintf("[proc] modulo %d mapeado en %x (%d paginas)\n", idx, base + off, pages);
    return base + off;
}

// Memoria anónima (ceros) para el hilo actual: devuelve la virtual del bloque.
fn proc_map_anon(id: u64, pages: u64) -> u64 {
    if id >= 32 || pages == 0 { return 0; }
    let used = (anon_next[id] - ANON_BASE) / 4096;
    if used > ANON_LIMIT_PAGES || pages > ANON_LIMIT_PAGES - used { return 0; }
    // Presupuesto conservador: datos, tablas PT y hasta tres tablas adicionales
    // (inicio no alineado a 2 MiB, PD y PDPT). El rango anónimo no cruza 1 GiB.
    // En el kernel uniprocesador esta syscall corre con IF=0 y no cede el CPU.
    let required = pages + (pages + 511) / 512 + 3;
    if required > pmm_free_count() { return 0; }
    let base = anon_next[id];
    vmm_map_new(sched_current_cr3(), base, pages, PTE_W | PTE_U);
    anon_next[id] = base + pages * 4096;
    return base;
}

fn proc_page_phys(cr3: u64, virt: u64) -> u64 {
    return vmm_translate(cr3, virt);
}

// image: dirección (virtual del kernel) del binario plano; size: bytes.
fn proc_spawn(name: str, image: u64, size: u64) -> u64 {
    // user.ld emits an end marker after .bss, so objcopy materializes all BSS
    // zeros in the flat image. Its actual size replaces the old 64 KiB guess.
    let image_limit = USER_STACK_TOP - USER_STACK_PAGES * 4096 - USER_BASE;
    if size == 0 || size > image_limit || image > 0xFFFFFFFFFFFFFFFF - size { return 0; }
    let code_pages = (size + 4095) / 4096;
    // Code + user stack + page tables + scheduler's 4-page kernel stack/XSAVE.
    let required = code_pages + USER_STACK_PAGES + (code_pages + 511) / 512 + 9;
    if required > pmm_free_count() { return 0; }
    let cr3 = vmm_new_space();
    vmm_map_new(cr3, USER_BASE, code_pages, PTE_W | PTE_U);
    var copied: u64 = 0;
    var vpage: u64 = 0;
    while copied < size {
        let phys = proc_page_phys(cr3, USER_BASE + vpage * 4096);
        var n = size - copied;
        if n > 4096 { n = 4096; }
        kmemcpy(pmm_phys_to_virt(phys), image + copied, n);
        copied += n;
        vpage += 1;
    }
    vmm_map_new(cr3, USER_STACK_TOP - USER_STACK_PAGES * 4096, USER_STACK_PAGES, PTE_W | PTE_U);
    let id = sched_create(name, USER_BASE, USER_STACK_TOP - 16, cr3, true);
    kprintf("[proc] '%s': %d bytes en %x, %d paginas de codigo, pila en %x\n",
            name, size, USER_BASE, code_pages, USER_STACK_TOP);
    return id;
}
