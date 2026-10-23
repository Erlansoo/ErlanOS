# Validación de M4 — 2026-09-14

## Resultado

M4 v0 pasó las pruebas en host y QEMU q35, 2 GiB, `-accel tcg -cpu max`, con
SmolLM2-135M-Instruct Q8_0 (144811360 bytes). Se comprobó selección real por logits,
IPC hacia el broker `system`, efectos del kernel y recibos correlacionados.

| Comprobación | Resultado |
|---|---|
| Reconstrucción forzada en paralelo: `make -s -j4 -B all` | PASS, sin avisos del compilador |
| `make test` | PASS (el panic de índice fuera de límites es intencional) |
| Wire: bytes de referencia, tamaños, versión, enum, flags, repetición | PASS |
| Kernel: derechos, objetos, buffers, regiones del compositor | PASS |
| Memoria anónima: overflow, falta de memoria y estado sin cambios al rechazar | PASS |
| Planner en host: 12 casos seleccionados, abstención y entradas fuera de límites | PASS |
| Planner: logits idénticos con/sin caché, repetición de prompt | PASS |
| QEMU: comandos directos, idempotencia y pruebas de rechazo | PASS |
| QEMU: cuatro acciones elegidas por el LLM y aplicadas | PASS |

## Acciones con inferencia real dentro del guest

La prueba incluyó los comandos directos y las pruebas negativas antes de estas
cuatro peticiones. El número de recibo crece incluso para peticiones rechazadas.

| Petición | Op | Recibo | Modo | Revisión | Tiempo de la petición |
|---|---:|---:|---|---:|---:|
| `/ai Read system status` | 1 | 16 | normal | 2 | 237.437 s |
| `/ai Enable focus mode` | 2 | 17 | foco | 3 | 23.210 s |
| `/ai Switch to normal mode` | 3 | 18 | normal | 4 | 27.815 s |
| `/ai Show status panel` | 4 | 19 | normal | 4 | 19.497 s |

Los tiempos incluyen tecleo y espera del test; la primera petición calcula todo
el prefijo, las siguientes reutilizan su KV. No son una comparación general de
rendimiento. La suite completa tardó 346.462 s y QEMU salió con código 0.

Prueba completa:

```bash
python scripts/qemu_m4_test.py --timeout 1800 --command-timeout 600 \
  --ai-query "Read system status" --ai-expected 1 \
  --ai-query "Enable focus mode" --ai-expected 2 \
  --ai-query "Switch to normal mode" --ai-expected 3 \
  --ai-query "Show status panel" --ai-expected 4
```

Evidencia local: `build/m4-test/20260914-120514-0ly9x41a/result.json`,
`serial.log` y `screen.png`. La consola de M3 limpia su zona al llenarse; por eso
la captura final conserva el panel y prompt, y los recibos completos están en el log.

Después se reconstruyó el kernel con la comprobación adicional de asignación
anónima y la generación agrupada de Make. La prueba determinista sobre esos
binarios finales pasó en 32.593 s, sin repetir inferencia no afectada por el cambio:
`build/m4-test/20260914-121051-_hdj2fgi/result.json`. La imagen `esp/` está actualizada.
Las dos VMs de prueba terminaron; no queda un monitor esperando en segundo plano.

## Límites observados

- La suite seleccionada no mide fiabilidad general. El modelo de 135M puede
  equivocarse fuera de estos ejemplos; las únicas acciones disponibles son las
  cuatro de esta ABI. No hay acceso a shell, ficheros o dispositivos por texto.
- Español: estado/foco/panel pasaron en host; `Vuelve al modo normal` se abstiene
  por el umbral conservador de cambios de modo. `/modo normal` es el atajo explícito.
- La caché redujo los pasos en host de 78 a 6–7. Los cinco logits de la prueba
  fría/con caché coincidieron exactamente. Log: `build/planner-host/self-test.log`.
- No se ha hecho una auditoría exhaustiva del kernel, tokenizer o compilador.
  Este hito habilita acciones acotadas, no automejora ni operación autónoma general.
