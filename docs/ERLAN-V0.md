# Erlan v0 — decisiones del lenguaje

Objetivo: control de sistemas cercano a C, seguridad de memoria moderna, y semántica
nativa para que una IA pueda observar, medir, modificar y verificar software.

## Se conserva de C
- Sin garbage collector, sin VM, sin runtime obligatorio.
- ABI C interoperable (`extern c { ... }`), salida ELF/`.bin`.
- Punteros crudos y MMIO sólo dentro de `unsafe`.
- Compilación AOT. El programador sabe aproximadamente qué instrucciones genera.

## Cambios respecto a C
| C | Erlan v0 |
|---|---|
| `int`, `long` | `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64` (después `f16 bf16 fp8<e4m3>`) |
| `int *p` | `ptr<i32>` (no nulo) / `unsafe ptr<i32>` / `ram`, `mmio`, `ioport` |
| `T *arr` sin longitud | slices `[]T` = `{ptr, len}` |
| `NULL` | `T?` obligatoriamente comprobado |
| códigos de error | `Result<T, E>` + `try` |
| goto cleanup | `defer` |
| `switch` | `match` con enums algebraicos |
| clases, herencia | `struct` + métodos + `interface`; composición |
| macros | `comptime { ... }` |
| `#define REG (*(volatile u32*)0x...)` | `register UART0 { base: 0x...; TX: u32 @ 0x00; }` |

## Nuevo, específico de ErlanOS (v1+, no en v0)
- Autoridad y efectos: `cap<File.Read>`, `cap<Device.Audio.Write>`, `secret<T>`, `tainted<T>`, efectos `pure io network device unsafe`.
- Tensores con shape/layout/placement: `tensor<f16, [B, T, 4096], layout=row_major, memory=auto>`.
- Cuantización con esquema: `qint<4, group=32, scheme=symmetric>`.
- `graph`, `kernel`, `@autotune`, `async` por dispositivo, `kv_cache<T>`, `model<T>`.
- Metadatos `.e.meta`: `source <-> EIR <-> binary`, efectos, capabilities, contratos de latencia.
- Resource contracts: `fn schedule_next() purpose "..." critical realtime max_latency 50us`.

## Bootstrap (estado: v0 funcionando)
1. `erlan/erlancompiler.py`: lexer, parser, verificación de tipos y emisión de C. **Hecho.**
   Soporta: tipos enteros/flotantes/bool/str, `ptr<T>`, `[]T`, `[N]T`, `struct`, `enum`, `const`,
   `fn`, `var`/`let`, `if`/`while`/`loop`/`for a..b`/`for a..=b`, `break`/`continue`/`return`,
   `defer`, `unsafe`, `match` con alternativas `A | B` y `_`, casts `as`, `&`/`*`, `a[i]`,
   `a[lo..hi]`, `.len`, literales de struct, `extern c { }` y `extern c "hdr.h" { }`.
2. Índices comprobados en slices y arrays (panic). Aritmética/indexación/casts de punteros sólo en `unsafe`.
3. Variables globales del módulo (`var x: T = ...;` a nivel superior, privadas al módulo). **Hecho.**
   Literales de cadena adyacentes se concatenan; `str` se indexa sólo en `unsafe`.
   Precedencia: `as` liga más fuerte que los binarios y más débil que los unarios (`&x as u64`).
   Un `for` sobre rango de literales itera en `u64`, como `.len`.
4. Módulos del kernel en Erlan: `fb gdt idt pic pit keyboard pmm heap fpu vmm sched ipc syscall proc`
   (M0..M2) y servicios de usuario `ulib init echo`. **Hecho.**
   Structs de otro módulo: `extern c "fb.h" { struct Fb { ... } fn ... }` registra el layout
   sin emitirlo. `erlan/runtime/umath.h` aporta bits<->f32, f16->f32, sqrt y lecturas
   little-endian; el resto de la matemática (exp, ln, sin, cos) está escrita en Erlan.
   Seguridad añadida tras revisión: `.len`/`.ptr` de slices y arrays son de sólo lectura; no se
   puede devolver `&local` ni una rebanada de un array local; literal-op-literal se evalúa en
   64 bits (`137 << 40` no desborda). `str` se castea como puntero sólo en `unsafe`.
5. `rmsnorm` ya compila y da resultado correcto en `erlan/tests/basics.e`; se enlaza al model runtime en M5.
6. Pendiente v0.x: literales de array, `pub` para exportar globales, `T?` opcional, `Result<T,E>` + `try`, métodos sobre struct, genéricos simples,
   `comptime`, `register`/`mmio`, tipos `f16`/`bf16`.
7. Cuando el subconjunto sea suficiente, `erlancompiler` se reescribe en Erlan (self-hosting).

## Ejemplo (ya compila)
```erlan
fn rmsnorm(x: []f32, w: []f32, out: []f32, eps: f32) {
    var ss: f32 = 0.0;
    for i in 0..x.len { ss += x[i] * x[i]; }
    ss = 1.0 / sqrt(ss / (x.len as f32) + eps);
    for i in 0..x.len { out[i] = w[i] * (x[i] * ss); }
}
```
