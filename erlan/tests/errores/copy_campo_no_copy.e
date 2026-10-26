// espera: no es Copy
struct Caja { v: i64; }
@copy struct Doble { a: Caja; b: Caja; }
fn main() -> i32 { return 0; }
