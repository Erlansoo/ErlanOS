// espera: a través de una referencia o puntero
struct Caja { v: i64; }
fn saca(r: &Caja) -> Caja { return *r; }
fn main() -> i32 { let c = Caja { v: 1 }; return saca(&c).v as i32; }
