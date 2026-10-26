// espera: es una variable global del módulo
struct Caja { v: i64; }
var caja_global: Caja = Caja { v: 1 };
fn main() -> i32 {
    let c = caja_global;
    return c.v as i32;
}
