# Erlan — lenguaje de sistemas para IA

Sucesor de C orientado al kernel y a la IA. Ver `../docs/ERLAN-V0.md`.

- `erlancompiler.py`  compilador bootstrap v0 (Python). Emite C que compila clang. Incluye la IR de
                      flujo de control (`Mir`) y los comprobadores de movimientos y préstamos del
                      modelo de memoria de Rust (`../docs/ERLAN-MEMORIA.md`).
- `runtime/erlan_rt.h` runtime mínimo freestanding: tipos, slices, comprobación de índices, `erlan_panic`.
- `tests/`            pruebas en host (`make test`): `basics.e`, `memoria.e` (salida exacta en
                      `memoria.esperado`) y `errores/*.e` (cada uno declara el error con el que debe fallar).

Uso:

```bash
python erlan/erlancompiler.py modulo.e -o build/gen/modulo.c --header build/gen/modulo.h
```

Camino del compilador: bootstrap en Python (hoy) -> `erlancompiler` reescrito en Erlan
(self-hosting) -> backend propio EIR -> LLVM -> MLIR cuando llegue GPU/NPU.

Primer módulo real del kernel en Erlan: `kernel/src/fb.e` (framebuffer, M0). Desde el 26/10/2026 el
kernel entero, los servicios y la red son Erlan (`import`, `asm`, varargs, atributos: `../docs/ERLAN-SISTEMA.md`);
los módulos se importan con `-I` (ver Makefile).
