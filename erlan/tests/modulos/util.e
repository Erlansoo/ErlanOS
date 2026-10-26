// Módulo importado por sistema.e: funciones, un struct @copy y un enum (la const es privada).
@copy struct Par { a: i64; b: i64; }
enum Modo { Lento, Rapido }
const ESCALA: i64 = 10;
fn par_suma(p: Par) -> i64 { return (p.a + p.b) * ESCALA; }
fn modo_nombre(m: Modo) -> str { match m { Modo.Lento => { return "lento"; } _ => { return "rapido"; } } return "?"; }
