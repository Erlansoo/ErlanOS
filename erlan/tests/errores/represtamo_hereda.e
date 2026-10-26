// espera: no se puede usar `a` mientras hay un préstamo &mut vivo
fn main() -> i32 {
    var a: i64 = 1;
    var b: i64 = 2;
    var p = &mut a;
    let q = &mut *p;   // hereda el préstamo de a
    p = &mut b;        // mata el camino *p, pero a sigue prestada por q
    a += 1;
    *q = 1;
    return a as i32;
}
