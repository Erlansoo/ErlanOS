// L1 — memoria virtual. Cada proceso tiene su PML4; la mitad superior (kernel,
// HHDM) se comparte copiando las entradas 256..511 del PML4 de arranque.

extern c "cpu.h" {
    fn cpu_read_cr3() -> u64;
    fn cpu_write_cr3(v: u64);
    fn cpu_invlpg(va: u64);
}
extern c "pmm.h" {
    fn pmm_alloc_page() -> u64;
    fn pmm_free_page(phys: u64);
    fn pmm_phys_to_virt(phys: u64) -> u64;
}
extern c "kmem.h" { fn kmemset(dst: u64, v: u8, n: u64); }
extern c "serial.h" { fn kprintf(fmt: str, ...); }
extern c "panic.h" { fn kpanic(msg: str); }

const PTE_PRESENT: u64 = 1;
const PTE_WRITE: u64 = 2;
const PTE_USER: u64 = 4;
const PTE_PWT: u64 = 8;
const PTE_PCD: u64 = 16;      // no cacheable: registros de dispositivo
const PTE_ADDR_MASK: u64 = 0x000FFFFFFFFFF000;

var kernel_cr3: u64 = 0;

fn vmm_init() {
    kernel_cr3 = cpu_read_cr3();
    kprintf("[vmm] pml4 del kernel en %x\n", kernel_cr3);
}

fn vmm_kernel_cr3() -> u64 { return kernel_cr3; }

fn table_virt(phys: u64) -> ptr<u64> {
    var p: ptr<u64>;
    unsafe { p = pmm_phys_to_virt(phys) as ptr<u64>; }
    return p;
}

// Nuevo espacio de direcciones: PML4 vacío en la mitad baja, kernel compartido arriba.
fn vmm_new_space() -> u64 {
    let phys = pmm_alloc_page();
    kmemset(pmm_phys_to_virt(phys), 0, 4096);
    let dst = table_virt(phys);
    let src = table_virt(kernel_cr3);
    unsafe {
        for i in 256..512 { dst[i] = src[i]; }
    }
    return phys;
}

// Devuelve la tabla del siguiente nivel apuntada por entry[idx], creándola si hace falta.
fn next_table(table: ptr<u64>, idx: u64, flags: u64) -> ptr<u64> {
    var e: u64 = 0;
    unsafe { e = table[idx]; }
    if (e & PTE_PRESENT) == 0 {
        let phys = pmm_alloc_page();
        kmemset(pmm_phys_to_virt(phys), 0, 4096);
        e = phys | PTE_PRESENT | PTE_WRITE | (flags & PTE_USER);
        unsafe { table[idx] = e; }
    } else if (flags & PTE_USER) != 0 && (e & PTE_USER) == 0 {
        // Un nivel superior sin bit U bloquearía el acceso de usuario.
        e = e | PTE_USER;
        unsafe { table[idx] = e; }
    }
    return table_virt(e & PTE_ADDR_MASK);
}

fn vmm_map(cr3: u64, virt: u64, phys: u64, flags: u64) {
    let pml4 = table_virt(cr3);
    let pdpt = next_table(pml4, (virt >> 39) & 511, flags);
    let pd = next_table(pdpt, (virt >> 30) & 511, flags);
    let pt = next_table(pd, (virt >> 21) & 511, flags);
    unsafe { pt[(virt >> 12) & 511] = (phys & PTE_ADDR_MASK) | flags | PTE_PRESENT; }
    if cr3 == cpu_read_cr3() { cpu_invlpg(virt); }
}

// Mapea `pages` páginas nuevas (ceros) a partir de virt. Devuelve la física de la primera.
fn vmm_map_new(cr3: u64, virt: u64, pages: u64, flags: u64) -> u64 {
    var first: u64 = 0;
    for i in 0..pages {
        let phys = pmm_alloc_page();
        kmemset(pmm_phys_to_virt(phys), 0, 4096);
        if i == 0 { first = phys; }
        vmm_map(cr3, virt + i * 4096, phys, flags);
    }
    return first;
}

// Traduce una dirección virtual del espacio cr3 a física (0 si no está mapeada).
fn vmm_translate(cr3: u64, virt: u64) -> u64 {
    var t = table_virt(cr3);
    var e: u64 = 0;
    unsafe { e = t[(virt >> 39) & 511]; }
    if (e & PTE_PRESENT) == 0 { return 0; }
    t = table_virt(e & PTE_ADDR_MASK);
    unsafe { e = t[(virt >> 30) & 511]; }
    if (e & PTE_PRESENT) == 0 { return 0; }
    t = table_virt(e & PTE_ADDR_MASK);
    unsafe { e = t[(virt >> 21) & 511]; }
    if (e & PTE_PRESENT) == 0 { return 0; }
    if (e & 0x80) != 0 { return (e & 0x000FFFFFFFE00000) + (virt & 0x1FFFFF); }   // página de 2 MiB
    t = table_virt(e & PTE_ADDR_MASK);
    unsafe { e = t[(virt >> 12) & 511]; }
    if (e & PTE_PRESENT) == 0 { return 0; }
    return (e & PTE_ADDR_MASK) + (virt & 0xFFF);
}

// Mapea un rango físico de dispositivo (MMIO) en la mitad de kernel, sin caché y
// sin bit de usuario. Se comparte con todos los procesos por el PML4 superior.
fn vmm_map_mmio(virt: u64, phys: u64, pages: u64) {
    for i in 0..pages {
        vmm_map(kernel_cr3, virt + i * 4096, phys + i * 4096, PTE_WRITE | PTE_PCD | PTE_PWT);
    }
}

fn vmm_switch(cr3: u64) {
    if cpu_read_cr3() != cr3 { cpu_write_cr3(cr3); }
}

// Validación sin desreferenciar memoria de usuario. Todos los niveles deben
// permitir usuario y, para destinos, escritura; también admite páginas grandes.
fn vmm_user_page_ok(cr3: u64, virt: u64, writable: bool) -> bool {
    var required = PTE_PRESENT | PTE_USER;
    if writable { required = required | PTE_WRITE; }
    var table = table_virt(cr3 & PTE_ADDR_MASK);
    var entry: u64 = 0;
    unsafe { entry = table[(virt >> 39) & 511]; }
    if (entry & required) != required { return false; }
    table = table_virt(entry & PTE_ADDR_MASK);
    unsafe { entry = table[(virt >> 30) & 511]; }
    if (entry & required) != required { return false; }
    if (entry & 0x80) != 0 { return true; }
    table = table_virt(entry & PTE_ADDR_MASK);
    unsafe { entry = table[(virt >> 21) & 511]; }
    if (entry & required) != required { return false; }
    if (entry & 0x80) != 0 { return true; }
    table = table_virt(entry & PTE_ADDR_MASK);
    unsafe { entry = table[(virt >> 12) & 511]; }
    return (entry & required) == required;
}

fn vmm_user_range_ok(cr3: u64, p: u64, len: u64, writable: bool) -> bool {
    let limit: u64 = 0x0000800000000000;
    if p == 0 || p >= limit { return false; }
    // Resta antes de sumar para rechazar overflow y el rango no canónico.
    if len > limit - p { return false; }
    if len == 0 { return true; }
    var page = p & 0xFFFFFFFFFFFFF000;
    let last = (p + len - 1) & 0xFFFFFFFFFFFFF000;
    loop {
        if !vmm_user_page_ok(cr3, page, writable) { return false; }
        if page == last { return true; }
        page += 4096;
    }
    return false;
}
