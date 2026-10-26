// espera: la elisión de Rust exige exactamente uno
fn elige(a: &i64, b: &i64) -> &i64 { if *a > *b { return a; } return b; }
fn main() -> i32 { let x: i64 = 1; let y: i64 = 2; return *elige(&x, &y) as i32; }
