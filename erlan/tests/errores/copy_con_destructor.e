// espera: no puede tener destructor
extern c "stdio.h" { fn printf(fmt: str, ...) -> i32; }
@copy struct Guardia { id: i64; }
@drop fn soltar(g: &mut Guardia) { printf("drop\n"); }
fn main() -> i32 { let g = Guardia { id: 1 }; return g.id as i32; }
