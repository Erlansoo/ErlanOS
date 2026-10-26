// espera: a través de una referencia compartida
fn main() -> i32 {
    var x: i64 = 1;
    let r = &x;
    *r = 5;
    return x as i32;
}
