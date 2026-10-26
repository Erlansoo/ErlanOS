// espera: no se puede prestar `x` como &mut: ya hay un préstamo &mut vivo
fn main() -> i32 {
    var x: i64 = 1;
    let a = &mut x;
    let b = &mut x;
    *a = 2;
    *b = 3;
    return x as i32;
}
