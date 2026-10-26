# Erlan — modelo de memoria (el de Rust), primera rebanada

Estado: implementada y probada el 26 de octubre de 2026 (`make test` en verde: `basics.e`,
`memoria.e` con salida exacta y 26 programas rechazados con el mensaje esperado; el kernel,
`net/` y los servicios compilan y arrancan igual). Decisión de partida en
[ERLAN-V0.md](ERLAN-V0.md) («Decisión 2026-10-26») y su contexto en
[ERLAN-REVISION-MEJORAS.md](ERLAN-REVISION-MEJORAS.md).

La especificación es la documentación de Rust: *The Rust Reference* (semántica de propiedad,
préstamos y `drop`), el *Rustonomicon* (`unsafe`, aliasing, *drop check*), la RFC 2094 (NLL:
préstamos con vida por flujo de control) y la *rustc dev guide* (MIR y *borrow check*). Este
documento dice **qué parte de eso ya tiene Erlan, con qué sintaxis, qué se adaptó y qué queda
para la siguiente rebanada**. Nada de lo que no esté en la sección «Qué hay hoy» se da por hecho.

## Qué hay hoy

### 1. Propiedad y movimiento

Cada valor tiene un único dueño: la variable (o campo, o parámetro) que lo contiene. Leer un
valor desde un lugar lo **copia** si su tipo es `Copy` y lo **mueve** si no lo es; tras moverlo,
el lugar de origen no se puede usar hasta que se vuelva a asignar.

| Tipo | ¿Copy? | Nota |
|---|---|---|
| `i8..u64`, `f32`, `f64`, `bool`, `str`, enums | sí | como en Rust |
| `ptr<T>` (puntero crudo) | sí | como `*const T` / `*mut T` |
| `&T` | sí | como en Rust |
| `&mut T` | **no** | se mueve; en una llamada se re-presta (ver préstamos) |
| `[N]T` | sí si `T` lo es | como en Rust |
| `[]T` (slice) | sí | **adaptación**: hoy es una vista cruda `{ptr, len}` sin dueño ni préstamo (siguiente rebanada) |
| `struct` | **sólo si se declara `@copy`** | como `#[derive(Clone, Copy)]`; todos sus campos deben ser Copy y no puede tener destructor |
| `struct` declarado en `extern c { }` | sí | datos planos de C, como los structs de *bindgen* |

```erlan
@copy struct Punto { x: i64; y: i64; }   // se copia
struct Guardia { id: i64; }              // se mueve

let a = Guardia { id: 1 };
consumir(a);        // a se movió a la función
printf("%lld", a.id);   // error: no se puede usar `a.v`: `a` fue movido en línea 5:13
```

Lo que el comprobador rechaza (con línea y columna del uso y del movimiento):

- usar o mover un valor ya movido, también si el movimiento fue en **una rama** (`if`) o en
  **una vuelta anterior de un bucle**; el mensaje lo indica;
- mover un campo de un struct **con destructor** (E0509 de Rust); mover un campo de un struct
  sin destructor es un movimiento parcial válido: el struct entero queda inutilizable, los
  demás campos siguen usables, y asignar el campo movido lo rehabilita;
- mover a través de una referencia o un puntero (`*r` con `T` no-Copy, E0507) y mover un
  elemento de array (E0508);
- asignar a un campo de un valor movido (E0382); asignar a la variable entera la vuelve a
  inicializar, también dentro de un bucle.

**Adaptación**: Erlan inicializa a cero toda variable declarada sin valor (`var g: Guardia;`),
así que un valor «sin inicializar» de Rust aquí es un valor cero vivo, con destructor si lo tiene.
Rust exige inicializar antes de usar; cambiarlo rompería todos los módulos actuales y se deja
para cuando `T?` y `Result` entren.

### 2. `drop`: liberación al salir del ámbito y su orden

Un struct declara su destructor con una función `@drop` que recibe `&mut` del tipo, igual que
`impl Drop for T { fn drop(&mut self) }`:

```erlan
struct Guardia { id: i64; }
@drop fn soltar(g: &mut Guardia) { printf("drop %lld\n", g.id); }
```

Reglas (las de Rust) y cómo se comprueban en `erlan/tests/memoria.e`:

1. Al salir de un bloque, las locales se liberan en **orden inverso de declaración**. Primero
   corren los `defer` del bloque (pueden usar las locales), después los destructores.
2. Un valor movido **no** se libera en su origen: lo libera su nuevo dueño (otra variable, el
   parámetro de la función llamada al terminar ésta, o quien reciba el `return`).
3. Movimiento condicional: en ejecución una **bandera de vida** por variable (`_erlan_live_x`
   en el C generado) decide; en compilación el comprobador impide cualquier uso posterior.
   Es la *drop elaboration* de rustc, sin la optimización que quita banderas innecesarias
   (clang las elimina cuando son constantes).
4. `return`, `break` y `continue` liberan lo que corresponde a los ámbitos que abandonan.
5. Asignar sobre un valor vivo lo libera primero y luego lo reemplaza.
6. Un struct **sin** `@drop` cuyos campos tengan destructor los libera en **orden de
   declaración** (*drop glue*, generado como `erlan_drop_<tipo>`); un struct **con** `@drop`
   corre primero su función y luego los campos. Los arrays liberan elemento por elemento.
7. Un temporal con destructor que no se guarda (`guardia(11);`) se libera al final de la
   sentencia.
8. Los parámetros se liberan al terminar la función, después de las locales. Las globales
   nunca se liberan (como los `static` de Rust).

`@copy` y `@drop` se excluyen (Copy y Drop en Rust).

### 3. Referencias `&T` y `&mut T`

```erlan
fn mayor(a: &mut Punto, b: &Punto) { if b.x > a.x { a.x = b.x; } }
fn primero(p: &Punto) -> &i64 { return &p.x; }

var a = Punto { x: 1, y: 2 };
let ra = &mut a;      // préstamo exclusivo
ra.x += 1;            // último uso de ra: el préstamo termina aquí (NLL)
let s1 = &a;          // ahora varias lecturas a la vez
let s2 = &a;
mayor(&mut a, &b);    // re-préstamo en la llamada: a sigue usable después
```

- `&lugar` da `&T`; `&mut lugar` da `&mut T` y exige que el lugar sea mutable: una variable
  `var`, o un camino a través de `&mut`/`ptr`; a través de `&T` no se escribe (ni `*r = v`, ni
  `r.f = v`, ni `r[i] = v`) y de una `let` no se toma `&mut`.
- Auto-deref como en Rust para campos e índices: `r.f`, `r[i]`, `r.len` con `r: &Struct`,
  `&[N]T`, `&[]T`.
- Coerciones de Rust: `&mut T` → `&T`, y `&T`/`&mut T` → `ptr<T>` (como `&x as *const T`,
  implícita en sitios de coerción). Por eso el código existente que hace `f(&x)` con
  `f(p: ptr<T>)` sigue compilando sin tocarlo.
- Una referencia nace de `&lugar`, `&mut lugar`, otra referencia o una llamada; no hay
  referencias nulas ni casts a referencia.
- Comparar referencias con `==` es error: se comparan los valores (`*a == *b`).

### 4. Préstamos con vida por flujo de control (NLL)

El comprobador sigue la RFC 2094 en su forma clásica: un préstamo está **vivo** en los puntos
del flujo donde alguna variable que lo contiene está viva (puede usarse después); la vida se
calcula hacia atrás sobre el grafo de flujo y «qué préstamos contiene cada referencia» hacia
adelante (asignaciones entre referencias, llamadas que devuelven referencias, re-préstamos).
Luego, en cada acceso:

| Acceso | Conflicto con un préstamo vivo que se solape |
|---|---|
| leer, `&` | sólo si el préstamo es `&mut` |
| escribir, mover, `&mut`, liberar (`drop`) | con cualquier préstamo |
| fin de ámbito | con cualquier préstamo del lugar: «no vive lo suficiente» |

«Solapar» es el criterio de Rust: prefijos de caminos de lugares; campos distintos de un struct
son disjuntos, dos índices del mismo array se solapan, y una escritura **superficial** a una
variable de referencia (reasignarla, o su fin de ámbito) no entra en conflicto con préstamos
tomados *a través* de ella (`let q = &mut *p; p = otra; *q = 1;` es válido, como en NLL).

Lo que se rechaza, cada uno con su programa en `erlan/tests/errores/`:

- dos `&mut` vivos del mismo lugar; `&` mientras hay un `&mut` vivo; `&mut` mientras hay un `&`;
- escribir, mover o usar el dueño mientras hay un `&mut` vivo; escribir mientras hay un `&`;
- devolver una referencia a una local o a un parámetro por valor (dos comprobaciones: la
  sintáctica de antes, para `ptr<T>`, y la del *borrow checker*: préstamos que llegan al valor
  de retorno y cuya raíz no es «lo que apunta un parámetro referencia»);
- una referencia que sobrevive al ámbito de lo que apunta (`r = &x;` dentro de un bloque);
- reasignar `*p` mientras un re-préstamo `&mut *p` sigue vivo; usar `p` después de moverlo con
  `let q = p;` (`&mut` se mueve, no se copia).

**Re-préstamo en llamadas**: pasar una variable `&mut T` a una función crea `&mut *p` que vive
lo que la llamada (o su resultado, si devuelve una referencia); `p` se puede usar después. Es
el *reborrow* implícito de Rust. Un re-préstamo **hereda** los préstamos de la referencia de la
que nace (la región del nuevo está contenida en la del original), y reasignar o terminar esa
referencia **mata** los préstamos tomados a través de ella (regla *kill* de NLL) sin soltar lo
heredado: `let q = &mut *p; p = otra;` deja `*p` libre, pero lo que `p` apuntaba sigue prestado
mientras `q` viva (`errores/represtamo_hereda.e`).

**Elisión de lifetimes** (las reglas de Rust, sin lifetimes explícitos todavía): una función
que devuelve una referencia debe tener **exactamente un** parámetro de referencia, del que
hereda la vida; con cero o varios se rechaza con un mensaje que remite a la siguiente rebanada.
No hay `&self`, así que la segunda regla de elisión no aplica aún.

**Globales**: `&global` y `&mut global` no crean préstamos (son `'static`, como en Rust; Rust
exige `unsafe` para `&mut static`; Erlan todavía no: siguiente rebanada, junto con la
mutabilidad interior).

**Punteros crudos y slices**: los accesos a través de `ptr<T>` y de `[]T` no crean préstamos ni
se comprueban (como los punteros crudos de Rust). Hoy `ptr<T>` se desreferencia fuera de
`unsafe`, como en Erlan v0: endurecerlo es parte de adaptar los módulos (rebanada siguiente).

### 5. La representación intermedia (equivalente al MIR)

`erlancompiler.py` construye por función, a partir del árbol de sintaxis **ya tipado**, una IR
de flujo de control (`class Mir`):

- **bloques básicos** con sentencias y un terminador (`goto`, `switch`, `return`);
- **lugares**: `(local, proyecciones)` con proyecciones de campo, índice y deref (de referencia
  o de puntero crudo);
- **operandos** `copy` / `move` / `const`, decididos por el tipo;
- **sentencias**: asignación de `use`, `ref`, `call` u operación; `init` (variable a cero),
  `drop` y `dead` (fin de vida) emitidos al salir de cada ámbito en orden inverso, con los
  `defer` inlineados antes, también en `return`, `break` y `continue`.

Sobre esa IR corren los dos análisis de flujo de datos: «quizá movido» (hacia adelante, con
caminos de campos para los movimientos parciales) y los préstamos NLL (vida hacia atrás,
préstamos contenidos hacia adelante, conflictos acceso por acceso). Si fallan, el C ya emitido
se descarta y el compilador imprime `archivo:línea:col: error: ...`.

**Lo que todavía no es como en rustc**: la emisión de C sigue recorriendo el árbol de sintaxis
(la IR decide si ese C se acepta y dónde van los destructores y sus banderas); en rustc el
código se genera desde el MIR. Pasar la emisión a la IR es trabajo de la siguiente rebanada y no
cambia lo que se comprueba.

## Qué queda para la siguiente rebanada

1. **Lifetimes explícitos** en firmas (`fn f<'a>(x: &'a T, y: &T) -> &'a T`) y en structs
   (`struct Vista<'a> { r: &'a T }`); hoy ambos casos se rechazan con un mensaje que lo dice.
   Con ellos, la regla de elisión de `&self` cuando lleguen los métodos.
2. **Slices como préstamos**: `&[T]` y `&mut [T]` con las reglas de aliasing; `a[lo..hi]` como
   re-préstamo del array. Hoy `[]T` es una vista cruda.
3. **Punteros crudos** como en Rust: desreferenciar `ptr<T>` sólo en `unsafe`, y las reglas de
   aliasing del Rustonomicon (Stacked/Tree Borrows) como contrato del código `unsafe`. Implica
   adaptar los módulos del kernel (`unsafe` marcado donde se toca hardware).
4. **`Send` y `Sync`** cuando el kernel tenga más de un hilo de ejecución real; **mutabilidad
   interior** (`Cell`, `RefCell`, `UnsafeCell`) y `&mut static` en `unsafe`.
5. **Inicialización obligatoria** (sin valores cero implícitos), diseñada junto con `T?` y
   `Result<T, E>` + `try`.
6. **Métodos y genéricos**, con `drop` como método (`fn drop(&mut self)`) en lugar de `@drop`.
7. **Emisión de C desde la IR** y *drop elaboration* con eliminación de banderas.
8. **Diagnósticos en JSON** con la posición y una sugerencia (ya se registra en
   `ERLAN-REVISION-MEJORAS.md`).

## Qué cambió en los módulos existentes

El kernel, `net/`, `mind/` y los servicios compilan con el nuevo comprobador y los binarios son
los mismos (mismo tamaño de `kernel.elf`). Hicieron falta cuatro retoques, todos de la forma
que pediría Rust:

- `kernel/src/ipc.e` y `kernel/src/sched.e`: `let e = &endpoints[ep]` / `let t = &threads[id]`
  escriben a través de la referencia → `&mut`.
- `services/system/workspace_store.e`: igual, `let n = &mut ws_nodes[id]`.
- `kernel/src/pci.e`: `PciBar` y `PciDevice` son datos planos que se copian de un array →
  `@copy`.

Todo lo demás pasó sin cambios: `&x` sigue coerciendo a `ptr<T>`, los slices siguen siendo
vistas, y lo que toca hardware sigue en `unsafe`.

## Cómo se comprueba

```bash
source scripts/env.sh
make test          # basics.e + memoria.e (salida exacta) + 26 programas rechazados
make all           # kernel y servicios con el nuevo comprobador
```

`erlan/tests/memoria.e` imprime el orden exacto de los destructores en cada caso (ámbitos,
`defer`, movimiento a función, condicional, retorno temprano, reasignación, campos, arrays,
bucles con `continue`, temporales) y los resultados de los préstamos; `memoria.esperado` es la
salida de referencia y `prueba_memoria.py` la compara. Cada archivo de `erlan/tests/errores/`
declara en su primera línea el mensaje con el que debe fallar, y la prueba exige línea y
columna.
