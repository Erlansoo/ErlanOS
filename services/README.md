# L2 — Servicios aislados

Procesos de usuario (Ring 3) con capabilities delegadas por el kernel. Cada servicio es
un directorio con `main.e`; el Makefile lo compila con erlancompiler, lo enlaza en
0x400000 (`user.ld`) y lo convierte a binario plano que Limine carga como módulo.

- `lib/usys.h`  la instrucción `int 0x80` (única pieza en C del lado usuario, junto a crt0)
- `lib/ulib.e`  syscalls envueltas, formato de números, log, send/recv
- `lib/crt0.c`  `_start`, `erlan_panic`, memset/memcpy
- `init/`       demo M2: Ring 3, cap denegada, estado SSE preservado, cinco pings por IPC
- `echo/`       recibe por el endpoint y registra; verifica su propio estado SSE
- `system/`     broker M4: valida AI Kernel API v0, consulta estado, cambia modo visual
               y presenta el panel con capabilities propias; devuelve recibos por IPC.
- `lib/ai_wire.e` serialización y validación pura de la ABI little-endian.

- `model/`      **model runtime** (M3), todo en Erlan:
  - `gguf.e`      parser GGUF v3 sobre el archivo mapeado (metadatos, tokenizer, tabla de tensores)
  - `tokenizer.e` BPE a nivel de bytes estilo GPT-2 con tablas hash sobre los strings del GGUF
  - `llama.e`     transformer Llama: RMSNorm, RoPE, GQA, SwiGLU, caché KV; pesos Q8_0/F16/F32
  - `mathx.e`     exp, ln, pow, sin, cos, silu en Erlan (sin libm)
  - `arena.e`     asignador bump sobre memoria anónima del kernel
  - `chat.e`      plantilla ChatML, generación greedy, salida token a token
  - `main.e`      mapea el GGUF, pide memoria, lee teclado y escribe en consola
  - `planner.e`   selección de acciones mediante logits restringidos; no ejecuta texto.
  - `mind/controller.e` (fuera de services): controlador L3 enlazado con model en M4.
  - `host/`       `platform.h` y `main.c` para correr el mismo código Erlan en el host
                  (`bash scripts/test_model_host.sh "Who are you?"`)

Capabilities (slots): 0 = Log, 1 = Time, 2 = Endpoint (init: send, echo: recv),
3 = Console (`model`: texto; `system`: presentación), 4 = Input, 5 = Module,
6 = Memory (estos tres sólo para `model`), 7 = System (`init`: read; `system`: read/write).
8/9 = endpoints privados petición/respuesta (`model`: send/recv; `system`: recv/send).

El compositor v0 conserva una zona de estado separada del texto. Próximos: compositor
general, Erlan compiler service y sandbox de candidatos. Detalles en `../docs/M4.md`.
