# Erlan — revisión de las mejoras propuestas

Revisión del 2026-10-26 de la sección «Erlan: sucesor de C orientado al kernel y a la IA» del informe
*Sistema operativo AI-native y Erlan* (`Downloads/deep-research-report (2).md`), comparada con lo que
[ERLAN-V0.md](ERLAN-V0.md) ya implementa.

## Mejora por mejora

| Mejora propuesta | Estado en Erlan v0 | Decisión |
|---|---|---|
| Sin GC ni VM, ABI de C, `unsafe`, enteros de ancho fijo, AOT | hecho | Se mantiene: es la base. |
| Referencias no nulas por defecto | `ptr<T>` no nulo; `T?` pendiente | Terminar `T?` en v0.x. |
| Ownership / borrowing | **no está en el diseño** | **Decidido (2026-10-26): el modelo completo de Rust, desde el día cero.** Ver «Seguridad de memoria». |
| `f16`, `bf16`, `fp8<e4m3>`, `fp8<e5m2>` | `f16 -> f32` en el runtime; tipos pendientes | Sí. En la RTX 3090 (Ampere) no hay unidades FP8: FP8 sirve para guardar pesos, no para calcular. |
| `qint<4, group=32, scheme=symmetric>` | pendiente | **Prioridad alta.** Coincide con los formatos GGUF que el runtime ya lee (Q8_0 y Q4_0 usan grupos de 32 con escala `f16`). |
| `tensor<T, [dims], layout, memory>` | pendiente | Sí, después de los genéricos. Empezar con forma fija; `memory` cuando haya GPU. |
| Capabilities y efectos (`cap<...>`, `pure io network device unsafe`, `secret/private/tainted`) | pendiente (v1) | **Lo que diferencia a Erlan.** Va antes que todo lo de GPU. |
| `model`, `kv_cache`, `graph`, `kernel` como palabras del lenguaje | pendiente | Como **biblioteca** sobre genéricos y `comptime`, no como palabras clave: lo que entra al lenguaje es muy difícil de cambiar después. |
| `kernel` + `@autotune` (precedente: Triton) | pendiente | Sí, cuando exista backend de GPU. Se conserva la función objetivo latencia + energía + memoria pico + error. |
| `async` con dataflow entre CPU, GPU y NPU | pendiente | Primero `async` sólo en CPU; el grafo entre dispositivos después. |
| Ubicación de memoria (`cpu`, `gpu(0)`, `shared<coherent>`) y `prefetch`/`pin`/`evict` | pendiente | El principio es correcto: mover la menor cantidad de bytes para ese hardware. Etapa de la 3090. |
| EIR como dialectos de MLIR | el README de `erlan/` dice «backend propio EIR -> LLVM -> MLIR» | **Decisión pendiente: el informe y el README se contradicen.** Recomendación: no crear un IR propio; emitir C (hoy), luego LLVM IR y MLIR cuando llegue la GPU. |

## Los dos puntos más importantes

### Seguridad de memoria: el modelo de Rust desde el día cero

Hoy Erlan comprueba índices, no deja devolver `&local` ni una rebanada de un array local, y limita los
punteros a `unsafe`. No evita usar memoria liberada, liberarla dos veces ni carreras entre hilos.

**Decisión del usuario (2026-10-26): Erlan adopta el modelo de memoria de Rust completo, con su
documentación como especificación de partida, y el sistema tiene que ser robusto desde el día cero.**
No se inventa un modelo propio ni una versión reducida: se estudia Rust y se adapta a Erlan.

Lo que entra, igual que en Rust:

- Propiedad, movimiento y liberación automática (*drop*), con *drop check*.
- Préstamos `&` (compartidos) y `&mut` (exclusivos): muchos lectores o un solo escritor.
- Lifetimes en firmas y structs, con inferencia y elisión como en Rust; comprobación por flujo (NLL).
- `Send` y `Sync` para hilos; mutabilidad interior explícita (equivalentes de `Cell`, `RefCell`, `UnsafeCell`).
- `unsafe` con las mismas reglas de aliasing que Rust documenta para el código inseguro.

Documentación de referencia:

| Documento | Para qué |
|---|---|
| *The Rust Reference* y la *Ferrocene Language Specification* (especificación de Rust) | Semántica exacta de propiedad, préstamos, lifetimes y `drop` |
| *The Rustonomicon* | `unsafe`, aliasing, `Send`/`Sync`, *drop check*, varianza |
| RFC 2094 (NLL) y Polonius | Cómo se comprueban los préstamos sobre el flujo de control |
| *rustc dev guide*: MIR y *borrow check* | Cómo lo implementa el compilador de Rust |
| Stacked Borrows / Tree Borrows (Miri) | Modelo de aliasing para el código `unsafe` |

Lo que implica para el compilador y el código existente:

1. `erlancompiler` necesita una **representación intermedia de flujo de control** (equivalente al MIR) entre
   el árbol de sintaxis y la emisión de C; ahí corre el *borrow checker*.
2. `T?`, `Result<T, E>` + `try`, métodos y genéricos se diseñan **junto con** los lifetimes, no antes y aparte.
3. Los módulos actuales del kernel y los servicios se adaptan: el código de hardware queda en bloques
   `unsafe` marcados y el resto pasa la comprobación.
4. Las **arenas** (el runtime del modelo ya usa `arena.e`) y los **handles con generación** se mantienen como
   herramientas para grafos y objetos del kernel, igual que se usan en Rust.

Estimación: varias semanas de trabajo, empezando después del hackathon de Meta (desde el 5 de noviembre).

**Avance (noche del 26 de octubre de 2026):** la primera rebanada ya está en `erlancompiler.py` y probada:
IR de flujo de control (equivalente al MIR), movimientos y `@copy`, `@drop` con el orden de Rust, `&T`/`&mut T`
con NLL y elisión de lifetimes; el kernel y los servicios compilan con cuatro retoques (`&mut` donde se escribe,
`@copy` en los structs de PCI). Lo hecho y lo pendiente, punto por punto: [ERLAN-MEMORIA.md](ERLAN-MEMORIA.md).

### Capabilities y efectos primero

Si cada función declara qué autoridad usa (`cap<File.Read>`, `network`, `device`) y qué efectos tiene,
el sistema puede **demostrar antes de ejecutar** qué pide un cambio hecho por la IA. Es lo que vuelve
segura la idea de una IA que agrega funciones o modifica programas.

`tainted<T>` sirve además para marcar datos que vienen de fuera (web, archivos, otras apps) e impedir que
lleguen sin control a acciones del sistema: una defensa contra la inyección de instrucciones.

## Lo que se agrega y el informe no menciona

- **Diagnósticos del compilador en JSON**, con la ubicación exacta y una sugerencia de arreglo, para que
  una IA corrija código sin interpretar texto.
- **Recarga en caliente de funciones**: reemplazar una función mientras el programa corre, con versión de
  módulo y migración de estado. Es la base técnica de «la IA le agrega funciones al programa en vivo».
- **Contratos** (precondiciones y postcondiciones) y **pruebas integradas** para verificar el código que
  escriba la IA antes de promoverlo.

## Orden propuesto

1. **Modelo de memoria de Rust** (propiedad, préstamos, lifetimes, `drop`, `Send`/`Sync`) con su representación
   intermedia de flujo de control, diseñado junto con `T?`, `Result<T, E>` + `try`, métodos, genéricos y `comptime`.
2. Adaptar los módulos existentes del kernel y los servicios al nuevo modelo.
3. Efectos y capabilities, sobre la propiedad: una capability es un valor que se mueve y no se copia.
4. Diagnósticos en JSON y recarga en caliente.
5. `qint`, `f16`, `bf16`.
6. Tensores.
7. GPU con CUDA en la 3090: `kernel` y `autotune`.
8. MLIR.

## Relación con Halo

La idea de los efectos se puede probar ya en Halo, antes de que exista en el compilador: cada capacidad
nueva que cree la IA lleva una ficha con lo que pide (por ejemplo «lee el proyecto, sin red») y Halo la
revisa antes de ejecutarla en el sandbox.
