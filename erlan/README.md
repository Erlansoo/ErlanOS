# Erlan — lenguaje de sistemas para IA

Sucesor de C orientado al kernel y a la IA. Ver `../docs/ERLAN-V0.md`.

- `erlancompiler.py`  compilador bootstrap v0 (Python). Emite C que compila clang.
- `runtime/erlan_rt.h` runtime mínimo freestanding: tipos, slices, comprobación de índices, `erlan_panic`.
- `tests/`            pruebas en host (`make test`).

Uso:

```bash
python erlan/erlancompiler.py modulo.e -o build/gen/modulo.c --header build/gen/modulo.h
```

Camino del compilador: bootstrap en Python (hoy) -> `erlancompiler` reescrito en Erlan
(self-hosting) -> backend propio EIR -> LLVM -> MLIR cuando llegue GPU/NPU.

Primer módulo real del kernel en Erlan: `kernel/src/fb.e` (framebuffer, M0).
