// espera: devuelve una referencia a la variable local `x`
struct Caja { v: i64; }
fn f(c: &Caja) -> &i64 {
    let x: i64 = c.v + 1;
    let r = &x;
    return r;
}
fn main() -> i32 { let c = Caja { v: 1 }; return *f(&c) as i32; }
