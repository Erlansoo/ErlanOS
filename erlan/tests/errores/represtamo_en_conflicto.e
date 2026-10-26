// espera: no se puede asignar a `*p` mientras está prestado
fn f(p: &mut i64) -> i64 {
    let q = &mut *p;
    *p = 1;
    *q = 2;
    return *p;
}
fn main() -> i32 { var x: i64 = 0; return f(&mut x) as i32; }
