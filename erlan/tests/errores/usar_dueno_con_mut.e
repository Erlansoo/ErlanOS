// espera: no se puede usar `x` mientras hay un préstamo &mut vivo
fn main() -> i32 {
    var x: i64 = 1;
    let r = &mut x;
    let y = x;
    *r = 2;
    return y as i32;
}
