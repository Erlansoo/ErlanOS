# Erlan — programación de sistemas: el kernel entero en Erlan

Estado: hecho el 26 de octubre de 2026. Desde este día `kernel/src/`, `kernel/entry/`,
`services/lib/` y `net/` no contienen ningún archivo C escrito a mano: lo que antes era
«pegamento» en C (instrucciones privilegiadas, accesos MMIO, puertos PCI, el puerto serie y
`kprintf`, el panic, el cambio de contexto, los 256 stubs de interrupción, el protocolo de
arranque Limine, la entrada `kmain`, el arranque `_start` de los servicios y la syscall
`int 0x80`) está escrito en Erlan. El pedido del usuario fue literal: «hacer de una vez todo
Erlan». Lo que sigue en C se lista al final, con su motivo.

## Qué se añadió al lenguaje

Todo esto está en `erlan/erlancompiler.py` y probado en `erlan/tests/sistema.e` (`make test`).

| Sintaxis | Qué hace | C que emite |
|---|---|---|
| `import modulo;` | Usa las funciones, structs y enums de `modulo.e`. Se busca junto al archivo que importa y en los `-I` del compilador; los imports son transitivos (los tipos que usa un módulo importado también se conocen). Las variables globales y las **constantes** siguen siendo privadas de cada módulo. | `#include "modulo.h"` (el header que el compilador genera para ese módulo) |
| `asm("plantilla" : salidas : entradas : clobbers);` | Ensamblador en línea con la sintaxis extendida de GNU, **sólo dentro de `unsafe`**. Los operandos son variables locales, parámetros o constantes del módulo. | `__asm__ volatile (...)` |
| `asm("...");` a nivel de módulo | Código ensamblador global: stubs, `_start`, `switch_context`. | `__asm__(...)` en el archivo |
| `fn f(fmt: str, ...) { vararg_start(); ... vararg_u64() ... vararg_end(); }` | Funciones variádicas propias (`kprintf`). `vararg_u64/i64/u32/i32/f64/str`. | `va_list`, `va_start`, `va_arg`, `va_end` |
| `@noreturn fn`, `@section("s")`, `@used`, `@volatile var` | Atributos de enlace: funciones que no vuelven (`kpanic`), variables en una sección (las peticiones de Limine), que no se eliminen, que no se cacheen. | `__attribute__((noreturn))`, `__attribute__((used, section("s")))`, `volatile` |
| `[a, b, c]` | Literal de array, sólo como inicializador (`var x: [3]u64 = [...]`, campos de struct, `const`, globales). | `{ a, b, c }` |
| `ptr<void>` | Puntero crudo sin tipo, para la ABI de C (`memcpy`). | `void *` |
| `extern c "x.h" { let tabla: [N][M]u8; }` | Datos definidos en C (las tablas de fuentes generadas), con índices comprobados. | nada: el header los define |
| `f as u64` | Dirección de una función (seguro, como `f as usize` en Rust). | `(u64)(uintptr_t)f` |

Las reglas del modelo de memoria ([ERLAN-MEMORIA.md](ERLAN-MEMORIA.md)) siguen aplicando: los
operandos de `asm` cuentan como lecturas y todo lo demás es `unsafe` explícito.

## Qué se reescribió

| Antes (C) | Ahora (Erlan) | Nota |
|---|---|---|
| `cpu.h`, `storage_io.h` | `kernel/src/cpu.e` | puertos, `cli/sti/hlt`, `lgdt/lidt` (descriptor armado byte a byte), recarga de segmentos, CR0–CR4, `invlpg`, `ltr`, XSAVE/XRSTOR, `xgetbv/xsetbv`, `cpuid`, `rdtsc` |
| `mmio.h` | `kernel/src/mmio.e` | cada acceso es un `mov` con clobber de memoria: no se reordena ni se combina |
| `pci_io.h` | `kernel/src/pci_io.e` | el shim privilegiado CF8/CFC; `tests/pci` audita su ensamblador generado (IF conservado, CF8 restaurado, una sola escritura a CFC) |
| `kmem.h` | `kernel/src/kmem.e` | `kmemcpy`, `kmemset`, `kstrlen` |
| `string.c` | `kernel/src/kstring.e` | `memcpy/memset/memmove/memcmp` que clang puede emitir en freestanding |
| `serial.c/.h` | `kernel/src/serial.e` | COM1 y `kprintf` variádico |
| `panic.c/.h` | `kernel/src/panic.e` | `kpanic` y `erlan_panic`, `@noreturn` |
| `switch.c/.h` | `kernel/src/switch.e` | `switch_context` y `thread_trampoline` en asm de módulo |
| `isr_stubs.c` (generado) | `kernel/src/isr_stubs.e` (generado por `scripts/gen_isr.py`) | 256 stubs alineados a 16 bytes: la dirección del vector n es `isr0 + 16n`, sin tabla de punteros |
| `limine.h` | `kernel/src/limine.e` | los structs del protocolo con el mismo layout; los punteros son `u64` y se convierten en `unsafe` |
| `main.c` | `kernel/src/boot.e` + `kernel/src/main.e` + `kernel/entry/probe_hardware.e` + `kernel/entry/probe_net.e` | las peticiones de Limine con `@section @used @volatile`; el arranque común en `boot.e`; cada imagen (normal, G0, red) tiene su `kmain`, en vez de `#ifdef` |
| `netplat.h` | `kernel/src/netplat.e` | la plataforma de la pila `net/` dentro del kernel |
| `font.h`, `docfont.h` | `kernel/src/font.e`, `kernel/src/docfont.e`, `services/lib/docfont.e` | la lógica de glifos en Erlan; las tablas siguen siendo datos generados |
| `services/lib/usys.h` | `services/lib/usys.e` | `int 0x80` |
| `services/lib/crt0.c` | `services/lib/crt0.e` | `_start` en asm de módulo, `erlan_panic` por syscall, rutinas de memoria |

Y los 46 módulos que declaraban a los demás con `extern c "x.h" { ... }` ahora dicen
`import x;`. El Makefile busca los módulos con `-Ikernel/src -Inet -Inet/tls` (kernel y red) y
`-Iservices/lib -Iservices/model -Imind` (servicios).

## Qué sigue sin ser Erlan, y por qué

| Pieza | Qué es | Por qué sigue así |
|---|---|---|
| `erlan/runtime/erlan_rt.h` | el runtime del compilador: tipos, slices, comprobación de índices, `erlan_panic` | pertenece al **backend C** del compilador, como `compiler-builtins` en Rust; desaparece cuando el backend deje de ser C |
| `kernel/src/font8x16.h`, `assets/fonts/font_data.h` | **datos** generados (bits de la fuente de consola, atlas de las fuentes de documentos) | son tablas, no código; `gen_font.ps1` y `gen_doc_fonts.py` podrían emitir `.e` con literales de array (la de consola es trivial; el atlas son 430 KB de literales) |
| `services/model/host/*.c`, `tests/**/*.c` | arneses de prueba en el anfitrión: cargan el GGUF desde disco, imprimen por stdout/stderr, miden con `clock()` | son pruebas, no el sistema; la plataforma de anfitrión del modelo sí es Erlan (`services/model/host/platform.e`) y les pide cuatro ganchos (`host_write`, `host_log`, `host_now_ms`, `host_cancelled`) porque `stdout`, `stderr` y `CLOCKS_PER_SEC` son macros de C |

El runtime del modelo también quedó en Erlan en la misma tarde: `services/model/umath.e`
(bits de `f32` por `movd`, `f16 → f32`, `sqrtss`, `cpuid`/`xgetbv` para decidir AVX2 con
`math_force_portable()` en lugar del macro de C, lecturas sin alinear) y las dos plataformas
`services/model/platform.e` (syscalls) y `services/model/host/platform.e` (anfitrión), elegidas
por el `-I` del compilador; `model_math.h` desapareció con `import llama`.
| `third_party/limine` | el cargador de arranque (C, de terceros, licencia BSD) | ErlanOS no lo reescribe: es el firmware el que lo carga; el protocolo sí está en Erlan (`limine.e`) |
| OVMF / UEFI de la placa | el firmware | no es parte del sistema |
| `erlan/erlancompiler.py` | el compilador bootstrap, en Python, que emite C para clang | por diseño, hasta el **self-hosting** (punto 8 de ERLAN-V0.md). El binario lo produce clang a partir del C generado, igual que Linux lo produce GCC a partir de C |
| Pruebas de host (`tests/*.c`, `erlan/tests/host_panic.c`) | arneses que compilan módulos Erlan en Windows con stubs | son pruebas, no el sistema |

Honestamente: «todo Erlan» hoy significa **todo el código fuente del sistema** (kernel,
servicios, red, biblioteca de usuario, arranque) en Erlan, con un compilador en Python y un
backend C. El siguiente peldaño real es el backend (emitir desde la IR, luego LLVM IR), y
después el self-hosting.

## Cómo se comprobó (26/10/2026)

- `make test`: `basics.e`, `memoria.e` (modelo de memoria) y `sistema.e` (import, asm con
  `rdtsc` y `cpuid`, varargs, literales de array, atributos, datos de C) en el anfitrión.
- `make all net-image hardware-image`: kernel, servicios e imágenes sin un `.c` propio;
  `llvm-objdump` muestra `.limine_requests` (0x150 bytes) y los stubs `isr0`, `isr1`, `isr2`
  a 16 bytes.
- QEMU: `scripts/qemu_m4_test.py` PASS (`build/m4-test/20261026-084910-uga0x28u`: arranque,
  modelo cargado, capabilities, recibos, `[model] m4: selftest PASS`) y `make test-net` PASS
  (`build/net-test/20261026-085159-w_z0kkjr`: DHCP, DNS, TCP, HTTP con la entrada
  `probe_net.e`).
- Pruebas de host adaptadas a los módulos Erlan: `scripts/test_m4_host.sh` (M4 y procesos con
  stubs), `scripts/test_pci_host.py` (inventario sintético y auditoría del ensamblador de
  `pci_io.e`), `scripts/test_xstate_host.py` (XSAVE con `cpu.e`), `test_pointer_host.py`,
  `test_workspace_host.py`, `test_document_host.py`, `test_input_host.py`.
- VirtualBox con el kernel nuevo: `scripts/vbox_erlanos.ps1 -Perfil red` PASS con `passed_all`
  (`build/vbox-erlanos/20261026-085502-red`: enlace, DHCP, DNS, TCP y HTTP) y `-Perfil qwen35`
  (`build/vbox-erlanos/20261026-085529-qwen35`: modelo cargado y `/estado` por PS/2).
