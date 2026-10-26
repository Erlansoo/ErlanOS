// espera: no se puede usar `p`: fue movido
fn f(p: &mut i64) -> i64 {
    let q = p;
    *q = 1;
    return *p;
}
fn main() -> i32 { var x: i64 = 0; return f(&mut x) as i32; }
