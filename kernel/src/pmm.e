// L1 — gestor de memoria física: bitmap de páginas de 4 KiB sobre el mapa de Limine.
// Las páginas se acceden por el HHDM (dirección física + offset) mientras no haya
// paginación propia (M2).

extern c "serial.h" { fn kprintf(fmt: str, ...); }
extern c "panic.h" { fn kpanic(msg: str); }

const PAGE_SIZE: u64 = 4096;
const LOW_RESERVED: u64 = 0x100000;   // 1 MiB

struct MemRegion {
    base: u64;
    len: u64;
    usable: bool;
}

var hhdm: u64 = 0;
var bitmap: ptr<u8>;          // 1 bit por página: 1 = ocupada
var bitmap_bytes: u64 = 0;
var total_pages: u64 = 0;
var free_pages: u64 = 0;
var last_alloc: u64 = 0;      // pista para búsquedas siguientes

fn bit_get(page: u64) -> bool {
    var v: u8 = 0;
    unsafe { v = bitmap[page / 8]; }
    return (v & ((1 as u8) << ((page % 8) as u8))) != 0;
}

fn bit_set(page: u64) {
    unsafe { bitmap[page / 8] = bitmap[page / 8] | ((1 as u8) << ((page % 8) as u8)); }
}

fn bit_clear(page: u64) {
    unsafe { bitmap[page / 8] = bitmap[page / 8] & ~((1 as u8) << ((page % 8) as u8)); }
}

fn pmm_phys_to_virt(phys: u64) -> u64 { return phys + hhdm; }
fn pmm_virt_to_phys(virt: u64) -> u64 { return virt - hhdm; }

fn pmm_init(regions: []MemRegion, hhdm_offset: u64) {
    hhdm = hhdm_offset;

    // 1. Límite superior de la memoria usable.
    var highest: u64 = 0;
    for i in 0..regions.len {
        if regions[i].usable {
            let top = regions[i].base + regions[i].len;
            if top > highest { highest = top; }
        }
    }
    total_pages = highest / PAGE_SIZE;
    bitmap_bytes = (total_pages + 7) / 8;

    // 2. Ubicar el bitmap en la primera región usable (sobre 1 MiB) que lo contenga.
    var placed = false;
    for i in 0..regions.len {
        if regions[i].usable && regions[i].base >= LOW_RESERVED && regions[i].len >= bitmap_bytes && !placed {
            unsafe { bitmap = (regions[i].base + hhdm) as ptr<u8>; }
            placed = true;
        }
    }
    if !placed { kpanic("pmm: no hay region para el bitmap"); }

    // 3. Todo ocupado; luego liberar lo usable.
    for b in 0..bitmap_bytes { unsafe { bitmap[b] = 0xFF; } }
    for i in 0..regions.len {
        if regions[i].usable {
            var first = regions[i].base / PAGE_SIZE;
            let end = (regions[i].base + regions[i].len) / PAGE_SIZE;
            // El primer MiB queda reservado (BIOS, trampolines SMP en M2+).
            if first < LOW_RESERVED / PAGE_SIZE { first = LOW_RESERVED / PAGE_SIZE; }
            for p in first..end { bit_clear(p); free_pages += 1; }
        }
    }

    // 4. Reservar las páginas del propio bitmap.
    var bm_phys: u64 = 0;
    unsafe { bm_phys = (bitmap as u64) - hhdm; }
    let bm_pages = (bitmap_bytes + PAGE_SIZE - 1) / PAGE_SIZE;
    for p in bm_phys / PAGE_SIZE..bm_phys / PAGE_SIZE + bm_pages {
        if !bit_get(p) { bit_set(p); free_pages -= 1; }
    }

    kprintf("[pmm] %d paginas, %d libres (%d MiB), bitmap %d KiB en %x\n",
            total_pages, free_pages, free_pages * PAGE_SIZE / (1024 * 1024),
            bitmap_bytes / 1024, bm_phys);
}

// Devuelve la dirección física de una página libre (y la marca ocupada).
fn pmm_alloc_page() -> u64 {
    return pmm_alloc_pages(1);
}

// n páginas físicamente contiguas.
fn pmm_alloc_pages(n: u64) -> u64 {
    var run: u64 = 0;
    var start: u64 = last_alloc;
    for p in last_alloc..total_pages {
        if bit_get(p) { run = 0; start = p + 1; }
        else {
            run += 1;
            if run == n {
                for q in start..start + n { bit_set(q); }
                free_pages -= n;
                last_alloc = start + n;
                return start * PAGE_SIZE;
            }
        }
    }
    if last_alloc != 0 { last_alloc = 0; return pmm_alloc_pages(n); }
    kpanic("pmm: sin memoria fisica");
    return 0;
}

fn pmm_free_page(phys: u64) {
    let p = phys / PAGE_SIZE;
    if !bit_get(p) { kpanic("pmm: doble liberacion"); }
    bit_clear(p);
    free_pages += 1;
    if p < last_alloc { last_alloc = p; }
}

fn pmm_free_count() -> u64 { return free_pages; }
fn pmm_total_count() -> u64 { return total_pages; }
