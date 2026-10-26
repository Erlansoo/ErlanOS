// espera: no se puede devolver una referencia a la variable local x
fn f(y: &i64) -> &i64 {
    let x: i64 = *y;
    return &x;
}
fn main() -> i32 { let v: i64 = 2; return *f(&v) as i32; }
