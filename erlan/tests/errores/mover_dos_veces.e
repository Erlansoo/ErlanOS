// espera: no se puede mover `a`: fue movido
struct Caja { v: i64; }
fn consumir(c: Caja) -> i64 { return c.v; }
fn main() -> i32 {
    let a = Caja { v: 1 };
    consumir(a);
    return consumir(a) as i32;
}
