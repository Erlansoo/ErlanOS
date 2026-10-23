# ErlanOS — Arquitectura por capas

> **La IA manda; el microkernel garantiza; el Immutable Core decide qué nunca puede violarse.**

Este documento fija las capas, las reglas que cruzan todas las capas y las decisiones
que el informe de investigación dejó como "no especificado". Es la referencia que
toda contribución debe respetar. Cambiar algo aquí es una decisión de arquitectura,
no un detalle de implementación.

## 1. Capas

```
L5  Intención del usuario        voz / texto / gesto  -> Trusted UI (• • • + halo)
L4  Capabilities de dominio      Electronics (KiCad nativo), CAD, MCU target packs, Code, Docs
L3  Cognitive Runtime            AI Mind · planner · memoria semántica · model router · AI Kernel API
L2  Servicios aislados           fs · dispositivos · red · audio · compositor · model runtime · Erlan compiler service
L1  Microkernel (Ring 0)         memoria virtual · interrupciones · scheduler · IPC · capabilities
L0  Immutable Core + boot        verifier · política de promoción · A/B rollback · llaves · Limine
    Hardware                     CPU · RAM · (GPU/NPU después) · storage · dispositivos
```

Cada capa sólo habla con la inmediatamente inferior a través de una interfaz
explícita. L3 nunca toca hardware; lo pide a L2 con una capability. L1 no razona;
sólo ejecuta mecanismos deterministas. L0 es diminuto y no se auto-modifica.

### L0 — Immutable Core
- Boot (Limine, UEFI), verificación de hashes de kernel y candidatos.
- Raíz de capabilities. Política de promoción/rollback. Slots A/B.
- **No es auto-modificable.** Cambiarlo requiere intervención humana fuera del sistema.

### L1 — Microkernel
- Memoria física y virtual (HHDM de Limine, paging propio después).
- GDT/IDT, interrupciones, timer, FPU/SSE/AVX habilitados y guardados en context switch (XSAVE).
- Scheduler determinista con clases: `REALTIME`, `INTERACTIVE`, `BACKGROUND`, `AI_HIGH`, `AI_LOW`.
- IPC síncrono por mensajes + memoria compartida.
- Capabilities tipadas: todo recurso se accede por handle no falsificable.
- Nada de lógica de IA aquí. Ningún peso de modelo vive en Ring 0.

### L2 — Servicios aislados
- Procesos de usuario con capabilities delegadas: fs, framebuffer/compositor, teclado, serial, virtio (después).
- **Model runtime**: inferencia CPU tipo ggml (GGUF, cuantización Q8_0 primero, Q4_0 después).
- **Erlan compiler service**: compila candidatos generados por la IA dentro del sistema.
- **Sandbox**: proceso aislado sin acceso a dispositivos donde se prueban candidatos.

### L3 — Cognitive Runtime
- AI Mind: identidad, memoria, objetivos, permisos. **Independiente del modelo**.
- Model router: tiny local -> main local -> nube (OpenAI/Claude) según privacidad, latencia, costo.
- Cognitive scheduler: C0 Observe · C1 Reflex · C2 Interactive · C3 Deep · C4 Evolve.
- AI Kernel API (ABI versionada sobre IPC, no syscalls de Ring 0):
  - `ai_query(query, context_ref, resource_profile)` — sin efectos.
  - `ai_memory(op, scope_cap)` — sólo memoria.
  - `ai_execute(action_plan, capability_set, tx_policy)` — acciones **estructuradas**, nunca shell.
  - `ai_present(spec, surface_cap)` — UI declarativa; el compositor es trusted.
- System Graph: el kernel y el compilador exponen nodos/aristas/métricas tipadas, no logs.

### L4 — Capabilities de dominio
Las "aplicaciones" son capabilities con API estructurada que la IA invoca directamente.
Ejemplo objetivo: **Electronics** (KiCad adaptado). La IA no ve píxeles; ve el grafo:
`Schematic -> Sheet -> Symbol -> Pin -> Net`, `Board -> Footprint -> Pad -> Track -> Zone`,
con operaciones `Net.Route`, `Footprint.Place`, `DRC.Run`, `Export.Gerber`.
Eso es "mejor visión de lo que hace": objetos y relaciones, no capturas de pantalla.

### L5 — Trusted UI
- Pantalla inicial: `• • •`. La interfaz aparece bajo demanda.
- El halo lo dibuja el compositor trusted; la IA no puede falsificar su estado
  (idle · listening · local reasoning · acting · cloud · authorization).

## 2. Reglas transversales (no negociables)

1. **Datos ≠ autoridad.** Encontrar un archivo o leer una instrucción en un documento nunca concede permisos.
2. **Todo efecto secundario pasa por una capability** verificable y deja recibo/audit ID.
3. **El LLM nunca corre en Ring 0.** Vive en un dominio aislado y reiniciable.
4. **Nada se promueve sin pasar por**: compile -> verify -> sandbox -> tests -> benchmark -> canary -> promote/rollback.
5. **`secret<T>` no cruza `cloud.send`** sin capability de egress explícita.
6. **Las API keys** viven en un credential service; el modelo puede decir `use_provider("claude")`, nunca leer la clave.
7. **Métricas antes que optimización**: ningún candidato se acepta por "más rápido" si es menos correcto.

## 3. Decisiones fijadas

| Tema | Decisión | Por qué |
|---|---|---|
| Arquitectura CPU | x86-64, QEMU q35, UEFI (OVMF) | Depuración con GDB, KVM/WHPX, hardware real después |
| Bootloader | Limine (protocolo Limine) | Framebuffer, HHDM, memmap y **módulos** listos; el GGUF se carga como módulo de boot, sin filesystem |
| Kernel L1 | C freestanding, clang + ld.lld | Cero dependencias; se migra función por función a Erlan |
| Hardware físico y GPU | Conservar el kernel propio; Linux/Nouveau/Mesa como referencias y bancos de prueba | Decisión del usuario, 2026-09-14. Adaptar interfaces a capabilities y servicios Erlan; no sustituir L1 por Linux. Ver `GPU-PASCAL.md` |
| Erlan bootstrap | Python -> emite C -> clang | Sin instalar nada nuevo; el bootstrap es desechable; luego self-hosting en Erlan |
| Erlan IR | EIR propio simple ahora; MLIR sólo cuando llegue GPU | MLIR cuesta horas de build y curva alta; no aporta en CPU-only |
| Modelo inicial | SmolLM2-135M-Instruct Q8_0; modelos mayores después | M3 demostró inferencia CPU en TCG con un GGUF de 145 MB; selección acotada en M4 |
| Formato de pesos | GGUF | Ecosistema llama.cpp; parser sencillo |
| Sandbox del optimizador | Proceso aislado por capabilities + tests diferenciales | La "VM hija QEMU dentro del guest" exige portar QEMU: se pospone |
| Virtualización host | WSL2 Ubuntu con KVM anidado (o QEMU Windows + WHPX) | Toolchain estándar, KVM real |
| Nombre | ErlanOS (SO), Erlan (lenguaje) | Provisional; clearance legal no realizado |

## 4. Roadmap por rebanadas verticales

Cada milestone atraviesa todas las capas que existen en ese momento y termina en algo que se ve en pantalla.

| M | Entregable visible | Capas tocadas |
|---|---|---|
| M0 | Arranca en QEMU, `• • •` en framebuffer, log serial, mapa de memoria. **Hecho.** | L0 L1 L5 |
| E0 | `erlancompiler` v0 y primer módulo del kernel en Erlan (`fb.e`). **Hecho.** | erlan L1 |
| M1 | GDT/IDT/excepciones, PIC, PIT, teclado, PMM bitmap, heap, FPU/SSE/AVX; nueve módulos en Erlan. **Hecho.** | L1 erlan |
| M2 | Paginación por proceso, Ring 3, TSS, `int 0x80`, scheduler con XSAVE, IPC por endpoints, capabilities por proceso; servicios `init` y `echo`. **Hecho.** | L1 L2 |
| M3 | Model runtime en Erlan (GGUF, BPE, Llama+KV) como servicio Ring 3; consola y teclado por capabilities; SmolLM2-135M responde en pantalla. **Hecho.** | L2 L5 |
| M4 | AI Kernel API v0 por IPC: instantánea de sistema, modo visual y panel; broker aislado, recibos y selección acotada por el modelo. **Prototipo hecho y probado.** Ver `M4.md` y `VALIDACION-M4.md` para alcance y evidencia. | L1 L2 L3 L5 |
| N0 | Driver e1000 (82540EM) en L1 como mecanismo; pila ARP/IPv4/UDP/DHCP/DNS en Erlan (`net/`); imagen de prueba resuelve un nombre real en QEMU con SLIRP. **Hecho.** Ver `RED.md`. | L1 net |
| M5 | `rmsnorm` y kernels matemáticos del model runtime escritos en Erlan; Erlan v0.x (`Result`, `T?`, métodos) | erlan L2 |
| M6 | Bucle de automejora: IA propone variante -> compile -> sandbox -> diff-test -> benchmark -> promote/rollback | L2 L3 |
| M7 | Semantic index mínimo, memoria episódica, cognitive scheduler | L3 |
| M8 | Capability Electronics v0: grafo esquemático/PCB estructurado (camino hacia KiCad nativo) | L4 |
| D0 | Primer dominio de trabajo estructurado: Documentos, sobre el lienzo común. Motor y API en Erlan, márgenes espejo, teclado, revisión/undo y borrador local. Exportación externa por puente temporal; almacenamiento y escritores nativos pendientes. Ver `DOCUMENTOS.md`. | L1 L2 L3 L4 L5 |
| M9+ | Router nube + `secret`/egress, GPU/NPU vía MLIR, voz, self-hosting de Erlan | todas |

## 5. Métricas mínimas (desde M3)

- Kernel: boot time, crashes en stress de 24 h.
- Capabilities: 0 ejecuciones no autorizadas en matriz allow/deny.
- Inferencia: TTFT, tok/s decode, RSS; ratio contra llama.cpp en el host.
- Automejora: speedup, regresiones, rollbacks; holdout de shapes no vistos.
- Prompt injection: suite tipo AgentDojo antes de habilitar nube o tools sensibles.
