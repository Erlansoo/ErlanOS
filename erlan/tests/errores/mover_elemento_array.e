// espera: no se puede mover un elemento de array
struct Caja { v: i64; }
fn main() -> i32 {
    var xs: [2]Caja;
    let c = xs[0];
    return c.v as i32;
}
