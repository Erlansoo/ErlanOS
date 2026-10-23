# ErlanOS

**An AI-native operating system, built from scratch.** The AI is the interface and the main
operator; the microkernel guarantees; an immutable core defines what can never be violated.
Every action the AI takes is structured, permission-checked and visible to the user, who can
review and change it.

- **Microkernel (x86-64, UEFI + Limine):** per-process paging, preemptive round-robin scheduler
  (PIT quantum, XSAVE context switch), Ring 3 processes, `int 0x80` syscalls, typed per-process
  capabilities and synchronous IPC.
- **Erlan language:** own systems language with a bootstrap compiler (`erlan/erlancompiler.py`,
  Erlan to C), used for the kernel, the user-space services and the network stack.
- **Native LLM inference inside the OS:** the Ring 3 `model` service maps a GGUF file, tokenizes and
  runs CPU inference (Q8_0). Supports Llama, Qwen2 and Qwen3.5 (GatedDeltaNet + GQA attention).
- **AI Kernel API v0:** structured actions over IPC to an isolated `system` service (never shell
  commands), with message validation, permission tests and execution receipts.
- **Networking:** Intel e1000 driver and ARP/IPv4/UDP/DHCP/DNS written in Erlan.
- **Halo**, the companion workspace where you watch the AI work live:
  - Claude Code chats per project tab (official CLI, permission buttons, Remote Control), and an
    ELP1 gateway so the chat inside ErlanOS can reach an agent on the host.
  - KiCad without MCP: lossless S-expression engine, schematic builder, A* wiring, ERC/netlist
    oracle, `pcbnew` board engine, Freerouting and copper zones.
  - ngspice simulation, living Markdown + LaTeX documentation (MiKTeX, pandoc), a FreeCAD + BMesh
    3D space and JLCPCB fabrication outputs.

Architecture: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md). Status, build steps and details follow
in Spanish.

---

## Descripción (español)

Sistema operativo AI-native construido por capas. La IA es la interfaz, el operador
principal y, con el tiempo, el optimizador de partes de sí misma. El microkernel
garantiza; la IA decide; el Immutable Core define lo que nunca se viola.

Ver [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) para las capas, las decisiones
fijadas y el roadmap por rebanadas verticales.

## Estado

- M0: kernel arranca en QEMU (UEFI + Limine), framebuffer con `• • •`, log por serial.
- Erlan v0: `erlancompiler` (bootstrap en Python) compila los módulos `.e` del kernel.
  Pruebas en host: `make test`.
- M1: GDT, IDT con manejo de excepciones, PIC, timer PIT, teclado PS/2, memoria física
  (bitmap), heap del kernel y SSE/AVX habilitados.
- M2: paginación por proceso, TSS, scheduler round-robin con guardado XSAVE, procesos en
  Ring 3 cargados como módulos de Limine, syscalls por `int 0x80`, capabilities por
  proceso e IPC síncrono por endpoints. Primeros servicios de L2 (`init`, `echo`) en Erlan.
- M3: **una IA corre dentro de ErlanOS**. El servicio `model` (Ring 3, en Erlan) mapea un GGUF
  cargado por Limine, tokeniza y ejecuta inferencia en la consola gráfica. El runtime
  Erlan admite Llama, Qwen2 y Qwen3.5 de texto (GatedDeltaNet + atención GQA).
  Modelo principal: Qwen3.5-2B Q8_0; perfil de código: Qwen2.5-Coder-1.5B-Instruct Q8_0.
- M4: AI Kernel API v0 por IPC hacia el servicio aislado `system`: consulta de estado,
  modo visual normal/foco y panel de estado del kernel. Recibos de ejecución, validación
  de mensajes y pruebas de permisos. Los comandos directos funcionan con todos los
  modelos; el clasificador experimental `/ai` sólo está validado con SmolLM2 y se
  abstiene con Qwen hasta validar su selección de acciones.
  Detalles, formato y límites en [docs/M4.md](docs/M4.md).
- N0: **ErlanOS llega a internet**. Driver Intel e1000 en L1 (`kernel/src/e1000.e`) y pila
  ARP/IPv4/UDP/DHCP/DNS en Erlan (`net/`). En QEMU con red de usuario obtiene dirección por
  DHCP y resuelve un nombre público a través del resolver real del host. Verificado por
  serial, captura pcap y el resolver del host: `make test-net`. Detalles en [docs/RED.md](docs/RED.md).

## Construir y ejecutar

Editor experimental **Documentos D0**: hoja sobre el lienzo café, redacción con
Qwen, edición con teclado y control nativo de papel, fuentes y márgenes espejo.
En PowerShell: `.\scripts\run_document_editor.ps1`. Dentro del SO: `crea un documento`
o `Redacta un informe breve sobre energia solar`. La edición vive en ErlanOS;
PDF/DOCX/ODT se convierten mediante un puente temporal en el anfitrión y el estado
del guest sigue en RAM. Alcance y comandos en [docs/DOCUMENTOS.md](docs/DOCUMENTOS.md).

El soporte actual está validado en QEMU. GPU de cómputo, internet e instalación
física siguen pendientes; evaluación del equipo y propuesta en
[docs/HARDWARE-REAL.md](docs/HARDWARE-REAL.md).

Windows (Git Bash):

```bash
bash scripts/setup-windows.sh   # una vez: LLVM, QEMU, make (winget) + Limine
source scripts/env.sh           # en cada shell nueva
make run                        # Qwen3.5-2B, disco GPT/FAT32 real, 4 GiB de RAM
# Cierra la VM anterior antes de cambiar de perfil:
make run MODEL=coder            # Qwen2.5-Coder-1.5B-Instruct
```

`make run` detecta WHPX en Windows o KVM en Linux/WSL y comprueba que QEMU puede
inicializarlo. Si no está disponible, informa la causa y usa TCG. Para WHPX hacen
falta virtualización en firmware y la característica Windows Hypervisor Platform;
el detector no cambia opciones del sistema. Comprueba la selección con
`python scripts/qemu_accel.py`. Para forzar emulación durante depuración:
`make run QEMU_ACCEL='-accel tcg -cpu max'`.

Los modelos seleccionados deben existir en `models/`; quedan fuera de git. La imagen
contiene un solo GGUF. Descargas verificadas, perfiles y límites en
[docs/QWEN.md](docs/QWEN.md). `make run MODEL=smollm` conserva el perfil histórico.
También se puede indicar un archivo compatible con `MODEL_FILE=models/archivo.gguf`.

Prueba del mismo runtime fuera de QEMU:
`bash scripts/test_chat_host.sh --model models/qwen3.5-2b-q8_0.gguf --semantic`.

Dentro de QEMU: `/estado`, `/modo foco`, `/modo normal`, `/panel`, `/ayuda`.
Para selección experimental con SmolLM2: `/ai Read system status`,
`/ai Enable focus mode`, `/ai Switch to normal mode`, `/ai Show status panel`.
El texto sin `/` sigue funcionando como chat. `/ai` reinicia el contexto de chat.
El chat usa un mensaje de sistema breve, sin ejemplos de respuestas, con
un máximo de 64 tokens por turno. `Enter` envía; `Shift+Enter` inserta una nueva
línea para escribir código (hasta 240 bytes por petición).
`Esc` detiene el chat o la interpretación `/ai`;
las otras teclas escritas durante la espera se conservan. La consola admite
acentos y ñ en UTF-8. Los modelos pequeños pueden equivocarse; pruebas actuales en
[docs/QWEN.md](docs/QWEN.md) e historial de SmolLM2 en [docs/CHAT-NATIVO.md](docs/CHAT-NATIVO.md).

TCG emula la CPU: los primeros tokens aún pueden tardar. El halo indica trabajo;
no hay una pausa artificial entre letras. `/estado` y `/panel` dan una respuesta
directa sin ejecutar inferencia.

Pruebas: `make test-m4-host` y `make test-m4`. Para incluir selección real del modelo:
`python scripts/qemu_m4_test.py --ai --model models/smollm2-135m-instruct-q8_0.gguf --timeout 1200 --command-timeout 600`.
Cada prueba usa una VM e imagen privadas y guarda recibos, resultado y captura en `build/`.
Regresión del chat: `bash scripts/test_chat_host.sh --model models/qwen3.5-2b-q8_0.gguf --semantic` y
`python scripts/qemu_chat_test.py --semantic --model models/qwen3.5-2b-q8_0.gguf --accel whpx --timeout 1500`.
Para probar con aceleración de Windows, añade `--accel whpx`.
Benchmark numérico: `python scripts/test_matmul_host.py --model models/smollm2-135m-instruct-q8_0.gguf`.

WSL2 Ubuntu (requiere virtualización activa en BIOS; da KVM):

```bash
bash scripts/setup-wsl.sh
make run
```

## Layout

```
kernel/    L1  microkernel (memoria, interrupciones, scheduler, IPC, capabilities)
services/  L2  servicios de usuario en Erlan (init, echo, system, model runtime)
mind/      L3  controlador de acciones M4; router y memoria después
caps/      L4  capabilities de dominio (Electronics/KiCad, MCU target packs, CAD)
erlan/     lenguaje Erlan: erlancompiler (bootstrap), runtime mínimo, pruebas
docs/      arquitectura, decisiones, specs
scripts/   setup y utilidades
```

### Espacio 3D de Halo

FreeCAD y BMesh mediante APIs nativas, en un lienzo común independiente de la PCB: [uso, contrato y límites](docs/HALO-ESPACIO-3D.md). Incluye proyectos persistentes, parámetros, historial y exportación nativa/STEP/STL. Abrir `http://127.0.0.1:8765/?pestana=espacio3d` con `python -m halo.servidor` (un solo servidor, puerto 8765).

### La IA dentro del Halo

Cada pestaña tiene sus chats de Claude Code (etapa 3 del plan): sesiones oficiales del binario sin modificar, permisos por botones, consola y Remote Control. Las conversaciones de agentes de la etapa de Codex se retiraron el 20/09/2026.
