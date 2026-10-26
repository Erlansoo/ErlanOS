// espera: no se puede usar `a.v`: `a` fue movido
struct Caja { v: i64; }
fn consumir(c: Caja) -> i64 { return c.v; }
fn main() -> i32 {
    let a = Caja { v: 1 };
    consumir(a);
    return a.v as i32;
}
