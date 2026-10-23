// Prueba de humo de Erlan v0: se compila en host y se ejecuta.
extern c "stdio.h" {
    fn printf(fmt: str, ...) -> i32;
}

const LIMIT: u64 = 8;

enum Estado { Idle, Listening, Acting }

struct Punto { x: i64; y: i64; }

struct Buffer { datos: [16]u8; usados: u64; }

fn suma(xs: []i32) -> i32 {
    var total: i32 = 0;
    for i in 0..xs.len { total += xs[i]; }
    return total;
}

fn manhattan(a: Punto, b: ptr<Punto>) -> i64 {
    var dx = a.x - b.x;
    var dy = a.y - b.y;
    if dx < 0 { dx = -dx; }
    if dy < 0 { dy = -dy; }
    return dx + dy;
}

fn nombre(e: Estado) -> str {
    match e {
        Estado.Idle => { return "idle"; }
        Estado.Listening | Estado.Acting => { return "activo"; }
        _ => { return "?"; }
    }
    return "?";
}

fn con_defer(n: i32) -> i32 {
    defer printf("  defer ejecutado (n=%d)\n", n);
    if n > 5 { return n * 2; }
    return n;
}

fn rmsnorm(x: []f32, w: []f32, out: []f32, eps: f32) {
    var ss: f32 = 0.0;
    for i in 0..x.len { ss += x[i] * x[i]; }
    ss = ss / (x.len as f32) + eps;
    // sin libm: aproximación 1/sqrt con Newton
    var inv: f32 = 1.0;
    for k in 0..12 { inv = inv * (1.5 - 0.5 * ss * inv * inv); }
    for i in 0..x.len { out[i] = w[i] * (x[i] * inv); }
}

fn main() -> i32 {
    var arr: [8]i32;
    for i in 0..LIMIT { arr[i] = (i as i32) * 3; }
    var s = arr[2..6];
    printf("suma(arr[2..6]) = %d (esperado 42)\n", suma(s));

    let p = Punto { x: 3, y: -4 };
    var q = Punto { x: -2, y: 1 };
    printf("manhattan = %lld (esperado 10)\n", manhattan(p, &q));

    printf("estado: %s %s\n", nombre(Estado.Idle), nombre(Estado.Acting));

    printf("con_defer(3) = %d\n", con_defer(3));
    printf("con_defer(9) = %d\n", con_defer(9));

    var b: Buffer;
    b.usados = 0;
    var k: u64 = 0;
    while k < 5 { b.datos[k] = (k as u8) + 65; b.usados += 1; k += 1; }
    b.datos[b.usados] = 0;
    unsafe {
        let pz = &b.datos[0];
        printf("buffer = %s (%llu bytes)\n", pz as str, b.usados);
    }

    var x: [4]f32; var w: [4]f32; var o: [4]f32;
    for i in 0..4 { x[i] = 1.0; w[i] = 2.0; }
    rmsnorm(x[..], w[..], o[..], 0.0);
    printf("rmsnorm -> %.3f %.3f (esperado 2.000)\n", o[0] as f64, o[3] as f64);

    // comprobación de límites: debe hacer panic
    var idx: u64 = 8;
    printf("accediendo arr[%llu]...\n", idx);
    arr[idx] = 1;
    printf("NO DEBERIA LLEGAR AQUI\n");
    return 1;
}
