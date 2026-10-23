// AI Kernel API v0: little-endian, sin punteros ni structs dependientes del ABI C.
// Request: 5 u64 (40 bytes). Response: 13 u64 (104 bytes). Ver docs/M4.md.
fn ai_put64(b: []u8, off: u64, value: u64) {
    for i in 0..8 { b[off + i] = ((value >> (i * 8)) & 255) as u8; }
}

fn ai_get64(b: []u8, off: u64) -> u64 {
    var value: u64 = 0;
    for i in 0..8 { value = value | ((b[off + i] as u64) << (i * 8)); }
    return value;
}

fn ai_encode(b: []u8, id: u64, op: u64) {
    ai_put64(b, 0, 1);
    ai_put64(b, 8, id);
    ai_put64(b, 16, op);
    ai_put64(b, 24, 0);
    ai_put64(b, 32, 0);
}

// Pure validation; no effect or authority can be requested through arbitrary text.
fn ai_validate(b: []u8, last: u64) -> u64 {
    if b.len != 40 { return 1; }
    if ai_get64(b, 0) != 1 { return 2; }
    let id = ai_get64(b, 8);
    if id == 0 || id <= last { return 3; }
    let op = ai_get64(b, 16);
    if op < 1 || op > 4 { return 4; }
    if ai_get64(b, 24) != 0 || ai_get64(b, 32) != 0 { return 5; }
    return 0;
}
