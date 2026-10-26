// espera: puede ser en una vuelta anterior del bucle
struct Caja { v: i64; }
fn consumir(c: Caja) -> i64 { return c.v; }
fn main() -> i32 {
    let a = Caja { v: 1 };
    var i: i64 = 0;
    while i < 3 { consumir(a); i += 1; }
    return 0;
}
