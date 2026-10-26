// Modelo de memoria de Rust en Erlan, primera rebanada (docs/ERLAN-MEMORIA.md).
// Casos que DEBEN compilar y producir exactamente la salida de memoria.esperado:
// propiedad y movimiento, @copy, @drop con su orden, préstamos & / &mut con vida por
// flujo (NLL), re-préstamos en llamadas, referencias devueltas desde parámetros.
extern c "stdio.h" {
    fn printf(fmt: str, ...) -> i32;
}

// Un recurso con destructor: imprime cuándo se libera.
struct Guardia { id: i64; }

@drop fn soltar(g: &mut Guardia) { printf("  drop Guardia %lld\n", g.id); }

fn guardia(id: i64) -> Guardia { return Guardia { id: id }; }

// Dato plano: se copia como un entero.
@copy struct Punto { x: i64; y: i64; }

// Struct sin @drop pero con campos con destructor: se liberan en orden de declaración.
struct Par { a: Guardia; b: Guardia; }

// 1. Orden de liberación: inverso al de declaración; el defer corre antes que los drops.
fn orden() {
    printf("orden: entra\n");
    let a = guardia(1);
    let b = guardia(2);
    defer printf("  defer de orden (a=%lld, b=%lld siguen vivas)\n", a.id, b.id);
    printf("orden: sale\n");
}

// 2. Mover a una función: el parámetro se libera dentro de la función llamada.
fn consumir(g: Guardia) -> i64 {
    printf("  consumir recibe %lld\n", g.id);
    return g.id;
}

// 3. Movimiento condicional: la bandera decide en ejecución; el comprobador garantiza
//    que no haya usos después.
fn condicional(mover: bool) {
    let g = guardia(3);
    if mover { consumir(g); }
    printf("  condicional(mover=%d) termina\n", mover as i32);
}

// 4. Retorno temprano y mover a otra variable: un solo dueño, una sola liberación.
fn temprano(n: i64) -> i64 {
    let g = guardia(4);
    if n > 0 { return n; }
    let h = g;
    printf("  h.id=%lld\n", h.id);
    return 0;
}

// 5. Asignar sobre un valor vivo lo libera primero (como Rust).
fn reasignar() {
    var g = guardia(5);
    g = guardia(6);
    printf("  reasignar: ahora g=%lld\n", g.id);
}

// 6. Drop glue: struct con campos con destructor y array de valores con destructor.
fn compuestos() {
    let p = Par { a: guardia(7), b: guardia(8) };
    var arr: [2]Guardia;
    arr[0] = guardia(9);
    arr[1] = guardia(10);
    printf("  compuestos: p.a=%lld arr[1]=%lld\n", p.a.id, arr[1].id);
}

// 7. Bucle: un valor creado en cada vuelta se libera en cada vuelta, también con continue;
//    mover y volver a inicializar dentro del bucle es válido.
fn bucle() {
    var acumulado = guardia(100);
    for i in 0..3 {
        let g = guardia(i as i64);
        if i == 1 { continue; }
        let viejo = acumulado;
        acumulado = guardia(viejo.id + 1);
    }
    printf("  bucle: acumulado=%lld\n", acumulado.id);
}

// 8. Temporal descartado: se libera al final de la sentencia.
fn temporal() {
    guardia(11);
    printf("  temporal: despues de la sentencia\n");
}

// 9. Préstamos. mayor recibe &mut y &; primero devuelve una referencia que nace del parámetro.
fn mayor(a: &mut Punto, b: &Punto) { if b.x > a.x { a.x = b.x; } }
fn primero(p: &Punto) -> &i64 { return &p.x; }
fn suma_ref(xs: &[4]i64) -> i64 { var t: i64 = 0; for i in 0..xs.len { t += xs[i]; } return t; }
fn incrementar(n: &mut i64) { *n += 1; }

fn prestamos() {
    var a = Punto { x: 1, y: 2 };
    let b = Punto { x: 5, y: 0 };
    let copia = a;             // @copy: a sigue utilizable
    let ra = &mut a;           // préstamo exclusivo...
    ra.x += 1;                 // ...cuyo último uso es este: aquí termina (NLL)
    let s1 = &a;               // y ahora dos lecturas compartidas a la vez
    let s2 = &a;
    printf("  prestamos: a.x=%lld (s1=%lld s2=%lld copia=%lld)\n", a.x, s1.x, s2.x, copia.x);
    mayor(&mut a, &b);         // re-préstamo en la llamada: a se puede usar después
    let px = primero(&a);      // referencia nacida de un parámetro (elisión de Rust)
    printf("  prestamos: mayor -> %lld, primero -> %lld\n", a.x, *px);
    a.y = 9;                   // px ya no se usa: a vuelve a ser escribible
    var n: i64 = 0;
    var k: i64 = 0;
    while k < 3 { let r = &mut n; incrementar(r); *r += 10; k += 1; }   // &mut en bucle: uno por vuelta
    var ys: [4]i64;
    ys[0] = 1; ys[1] = 2; ys[2] = 3; ys[3] = 4;
    printf("  prestamos: n=%lld suma=%lld a.y=%lld\n", n, suma_ref(&ys), a.y);
}

// 10. Reasignar una referencia no invalida un re-préstamo tomado a través de ella (NLL: la
//     escritura es superficial), y un movimiento parcial deja usables los otros campos.
struct Caja { v: i64; }
struct Dos { a: Caja; b: Caja; }
fn reasigna_ref(p: &mut i64, otra: &mut i64) -> i64 {
    let q = &mut *p;
    p = otra;
    *q = 1;
    *p += 10;
    return *q + *p;
}
fn parciales() {
    var d = Dos { a: Caja { v: 1 }, b: Caja { v: 2 } };
    let a = d.a;           // d queda parcialmente movido
    printf("  parciales: a=%lld d.b=%lld\n", a.v, d.b.v);
    d.a = Caja { v: 3 };   // vuelve a estar entero
    let e = d;
    printf("  parciales: e.a=%lld e.b=%lld\n", e.a.v, e.b.v);
    var x: i64 = 0;
    var y: i64 = 5;
    printf("  parciales: reasigna_ref=%lld\n", reasigna_ref(&mut x, &mut y));
}

fn main() -> i32 {
    orden();
    printf("consumir:\n");
    let g = guardia(0);
    let id = consumir(g);
    printf("  consumir devolvio %lld\n", id);
    printf("condicional:\n");
    condicional(true);
    condicional(false);
    printf("temprano:\n");
    printf("  temprano(1)=%lld\n", temprano(1));
    printf("  temprano(0)=%lld\n", temprano(0));
    printf("reasignar:\n");
    reasignar();
    printf("compuestos:\n");
    compuestos();
    printf("bucle:\n");
    bucle();
    printf("temporal:\n");
    temporal();
    printf("prestamos:\n");
    prestamos();
    printf("parciales:\n");
    parciales();
    printf("fin\n");
    return 0;
}
