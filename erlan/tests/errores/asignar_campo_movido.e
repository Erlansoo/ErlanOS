// espera: no se puede asignar a `a.v`: `a` fue movido
struct Caja { v: i64; }
fn consumir(c: Caja) -> i64 { return c.v; }
fn main() -> i32 {
    var a = Caja { v: 1 };
    consumir(a);
    a.v = 2;
    return 0;
}
