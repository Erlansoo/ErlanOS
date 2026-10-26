// espera: no se puede mover `a` mientras está prestado
struct Caja { v: i64; }
fn consumir(c: Caja) -> i64 { return c.v; }
fn main() -> i32 {
    let a = Caja { v: 1 };
    let r = &a;
    consumir(a);
    return r.v as i32;
}
