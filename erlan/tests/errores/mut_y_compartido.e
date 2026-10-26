// espera: no se puede prestar `a` como & mientras hay un &mut vivo
struct Caja { v: i64; }
fn main() -> i32 {
    var a = Caja { v: 1 };
    let r = &mut a;
    let s = &a;
    r.v = 2;
    return s.v as i32;
}
