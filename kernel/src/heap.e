// L1 — heap del kernel: lista de bloques first-fit sobre una arena contigua
// tomada del PMM. Suficiente para estructuras del kernel; el model runtime (L2)
// usará su propio arena de tensores.

extern c "serial.h" { fn kprintf(fmt: str, ...); }
extern c "panic.h" { fn kpanic(msg: str); }
extern c "pmm.h" {
    fn pmm_alloc_pages(n: u64) -> u64;
    fn pmm_phys_to_virt(phys: u64) -> u64;
}

const HEAP_INITIAL_PAGES: u64 = 4096;    // 16 MiB
const HEAP_ALIGN: u64 = 16;
const BLOCK_MAGIC: u32 = 0x45524C4E;     // "ERLN"

struct Block {
    magic: u32;
    free: u32;
    size: u64;      // bytes útiles después de la cabecera (múltiplo de 16)
    next: u64;      // dirección del siguiente bloque, 0 = fin
    pad: u64;       // cabecera de 32 bytes: el payload queda alineado a 16
}

const BLOCK_HDR: u64 = 32;

var heap_start: u64 = 0;
var heap_end: u64 = 0;
var allocs: u64 = 0;
var frees: u64 = 0;

fn block_at(addr: u64) -> ptr<Block> {
    var b: ptr<Block>;
    unsafe { b = addr as ptr<Block>; }
    return b;
}

fn heap_init() {
    let phys = pmm_alloc_pages(HEAP_INITIAL_PAGES);
    heap_start = pmm_phys_to_virt(phys);
    heap_end = heap_start + HEAP_INITIAL_PAGES * 4096;
    let first = block_at(heap_start);
    first.magic = BLOCK_MAGIC;
    first.free = 1;
    first.size = heap_end - heap_start - BLOCK_HDR;
    first.next = 0;
    kprintf("[heap] arena %d MiB en %x\n", HEAP_INITIAL_PAGES * 4096 / (1024 * 1024), heap_start);
}

fn align_up(v: u64, a: u64) -> u64 { return (v + a - 1) & ~(a - 1); }

fn kalloc(size: u64) -> u64 {
    let need = align_up(size, HEAP_ALIGN);
    var addr = heap_start;
    while addr != 0 {
        let b = block_at(addr);
        if b.magic != BLOCK_MAGIC { kpanic("heap: bloque corrupto"); }
        if b.free == 1 && b.size >= need {
            // dividir si sobra espacio para otro bloque
            if b.size >= need + BLOCK_HDR + HEAP_ALIGN {
                let rest_addr = addr + BLOCK_HDR + need;
                let rest = block_at(rest_addr);
                rest.magic = BLOCK_MAGIC;
                rest.free = 1;
                rest.size = b.size - need - BLOCK_HDR;
                rest.next = b.next;
                b.size = need;
                b.next = rest_addr;
            }
            b.free = 0;
            allocs += 1;
            return addr + BLOCK_HDR;
        }
        addr = b.next;
    }
    kpanic("heap: sin memoria");
    return 0;
}

fn kfree(p: u64) {
    if p == 0 { return; }
    let b = block_at(p - BLOCK_HDR);
    if b.magic != BLOCK_MAGIC { kpanic("heap: kfree de puntero invalido"); }
    if b.free == 1 { kpanic("heap: doble kfree"); }
    b.free = 1;
    frees += 1;
    // fusionar con el siguiente si está libre
    if b.next != 0 {
        let n = block_at(b.next);
        if n.free == 1 {
            b.size = b.size + BLOCK_HDR + n.size;
            b.next = n.next;
        }
    }
}

fn heap_selftest() {
    let a = kalloc(100);
    let b = kalloc(2000);
    let c = kalloc(64);
    kfree(b);
    let d = kalloc(1000);   // debe reutilizar el hueco de b
    let aligned = (a % 16 == 0) && (b % 16 == 0) && (c % 16 == 0) && (d % 16 == 0);
    let ok = d == b && aligned;
    kprintf("[heap] selftest a=%x b=%x c=%x d=%x reutiliza=%d alineado16=%d\n",
            a, b, c, d, (d == b) as u64, aligned as u64);
    if !ok { kpanic("heap: selftest fallo"); }
    kfree(a); kfree(c); kfree(d);
}
