// Arena de memoria del model runtime: asignación por bump sobre un bloque grande
// (memoria anónima pedida al kernel, o malloc en host). Nunca libera: el runtime
// reserva todo al cargar el modelo.

extern c "platform.h" { fn platform_log(s: str); }

var base: u64 = 0;
var size: u64 = 0;
var used: u64 = 0;

fn arena_init(b: u64, s: u64) { base = b; size = s; used = 0; }

fn arena_used() -> u64 { return used; }
fn arena_size() -> u64 { return size; }

fn arena_alloc(bytes: u64) -> u64 {
    let aligned = (used + 63) & ~63;
    if aligned + bytes > size {
        platform_log("arena: sin memoria");
        return 0;
    }
    used = aligned + bytes;
    return base + aligned;
}

fn arena_f32(n: u64) -> []f32 {
    let a = arena_alloc(n * 4);
    var s: []f32;
    unsafe { let p = a as ptr<f32>; s = p[0..n]; }
    for i in 0..n { s[i] = 0.0; }
    return s;
}

fn arena_u32(n: u64) -> []u32 {
    let a = arena_alloc(n * 4);
    var s: []u32;
    unsafe { let p = a as ptr<u32>; s = p[0..n]; }
    for i in 0..n { s[i] = 0; }
    return s;
}

fn arena_u64(n: u64) -> []u64 {
    let a = arena_alloc(n * 8);
    var s: []u64;
    unsafe { let p = a as ptr<u64>; s = p[0..n]; }
    for i in 0..n { s[i] = 0; }
    return s;
}

fn arena_u8(n: u64) -> []u8 {
    let a = arena_alloc(n);
    var s: []u8;
    unsafe { let p = a as ptr<u8>; s = p[0..n]; }
    for i in 0..n { s[i] = 0; }
    return s;
}
