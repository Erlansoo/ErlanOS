// espera: es inmutable (let)
fn main() -> i32 {
    let x: i64 = 1;
    let r = &mut x;
    return *r as i32;
}
