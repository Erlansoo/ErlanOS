// espera: tiene destructor (Rust E0509)
extern c "stdio.h" { fn printf(fmt: str, ...) -> i32; }
struct Guardia { id: i64; }
@drop fn soltar(g: &mut Guardia) { printf("drop %lld\n", g.id); }
struct Par { a: Guardia; b: Guardia; }
@drop fn soltar_par(p: &mut Par) { printf("drop par\n"); }
fn main() -> i32 {
    let p = Par { a: Guardia { id: 1 }, b: Guardia { id: 2 } };
    let a = p.a;
    return a.id as i32;
}
