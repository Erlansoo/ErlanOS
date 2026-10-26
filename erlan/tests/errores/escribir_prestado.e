// espera: no se puede asignar a `x` mientras está prestado
fn main() -> i32 {
    var x: i64 = 1;
    let r = &x;
    x = 2;
    return *r as i32;
}
