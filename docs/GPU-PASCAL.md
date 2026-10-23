# Cómputo Pascal con el kernel propio

Fecha: 2026-09-14. Decisión confirmada por el usuario: ErlanOS conserva su kernel.
Linux puede servir para estudiar y medir el hardware; el producto sigue arrancando
con L1 propio. Esta investigación no constituye un controlador funcionando.

## Conclusión inicial

Conviene partir de documentación de NVIDIA, Nouveau y Mesa. La ingeniería inversa
queda para lagunas concretas que impidan una prueba, después de identificar qué
se conoce. Traducir un controlador completo no es el primer paso.

Objetivo de hardware: GTX 1050 Ti, PCI `10de:1c82`, familia Pascal/GP107,
4096 MiB de VRAM y compute capability 6.1. La capacidad y memoria se comprobaron
con `nvidia-smi`. ErlanOS ya identificó el PCI `10de:1c82` durante el
[diagnóstico físico G0](PRUEBA-FISICA-G0.md). La lectura de registros internos
para confirmar el chipset GP107 por MMIO sigue pendiente.

## Referencias concretas revisadas

Se descargaron once archivos de referencia, sin enlazarlos en el kernel.
El manifiesto local incluye URL, tamaño y SHA256 de cada archivo:
`build/gpu-research/20260914/manifest.json`.

- Linux/Nouveau: revisión `587858367581b9c55c3690f4e63382ad622719d4`.
- NVIDIA/open-gpu-doc: revisión `9fdf5c4062007929d9f4e6cbad9c9771fe61b880`.

| Pieza | Hallazgo del código/documentación | Aplicación al port |
|---|---|---|
| Nouveau `engine/device/base.c` | `nv137_chipset` identifica GP107 y combina componentes de varias generaciones | Mapa de dependencias; no basta copiar el archivo llamado GP107 |
| Nouveau `engine/gr/gp107.c` | Expone `PASCAL_COMPUTE_B` y enumera firmware FECS/GPCCS y tablas de inicialización | Inicialización del motor y requisitos de firmware |
| Nouveau `subdev/acr/gp102.c` | Incluye carga ACR para GP107 y procesamiento de firmas | Identificar imágenes originales y secuencia de arranque |
| Nouveau `subdev/mmu/gp100.c`, `vmmgp100.c` | Gestión de memoria virtual de GPU separada de las tablas x86 | Diseñar mapeos, coherencia y vida útil de buffers |
| Nouveau `engine/fifo/gp100.c` | Implementación de la cola de trabajo Pascal | Estudiar canales, envío y finalización |
| NVIDIA `classes/compute/clc1c0.h` | Define la clase `PASCAL_COMPUTE_B` (`0xC1C0`) y sus métodos | Referencia pública del protocolo de comandos |
| NVIDIA `pascal/gp100-msi-intr.txt` | Documentación de interrupciones Pascal | Referencia que requiere contrastar aplicabilidad a GP107 |

Fuentes: [Nouveau GP107](https://github.com/torvalds/linux/blob/587858367581b9c55c3690f4e63382ad622719d4/drivers/gpu/drm/nouveau/nvkm/engine/gr/gp107.c),
[selección de componentes](https://github.com/torvalds/linux/blob/587858367581b9c55c3690f4e63382ad622719d4/drivers/gpu/drm/nouveau/nvkm/engine/device/base.c),
[métodos de cómputo NVIDIA](https://github.com/NVIDIA/open-gpu-doc/blob/9fdf5c4062007929d9f4e6cbad9c9771fe61b880/classes/compute/clc1c0.h).

[Mesa/NVK](https://docs.mesa3d.org/drivers/nvk.html) documenta soporte para Kepler
y posteriores, incluyendo Pascal, y requiere Linux 6.6 como mínimo en su
integración existente. Esto aporta una referencia funcional; no demuestra que
NVK pueda ejecutarse directamente sobre nuestras syscalls.

## Dificultad principal para alcanzar rendimiento

La [matriz de energía de Nouveau](https://nouveau.freedesktop.org/PowerManagement.html)
marca como pendientes las funciones de cambio de frecuencia de motor y memoria
de NV130/Pascal. La propia página advierte que es una aproximación al estado.
Además, la ruta PMU `gp102.c` revisada ofrece una interfaz sin firmware operativo
para esa gestión. Por tanto, soporte de comandos de cómputo no prueba acceso al
rendimiento máximo. Se deben medir los relojes reales y el tiempo de ejecución.

La traducción de C a Erlan no elimina dependencias de firmware ni cambia sus
requisitos de firma. El firmware, cuando corresponda, conserva su formato de
máquina original. No se ha descargado ni modificado firmware en este estudio.

## Qué significa adaptarlo a Erlan

- L1 mantiene el control de dispositivos, MMIO, interrupciones y memoria para DMA.
- Un servicio L2 de GPU, con permisos acotados, administra buffers, contextos,
  colas, esperas y recuperación. El modelo solicita operaciones estructuradas;
  no recibe acceso arbitrario a registros o direcciones físicas.
- La lógica de control se implementa en `.e`, con el pegamento de máquina mínimo
  necesario. Las dependencias de asignación de memoria, sincronización y firmware
  se sustituyen por interfaces de ErlanOS; no por una traducción textual.
- El programa que corre en los núcleos GPU necesita instrucciones para Pascal.
  El compilador actual Erlan→C→x86 no las genera. La primera prueba puede usar un
  programa GPU precompilado y verificado; un backend de compilación es otro hito.

Cada componente reutilizado conserva procedencia y sus avisos de licencia.
Los archivos de referencia permanecen separados del código de producción.

## Primeras pruebas y condiciones de avance

| Hito | Entregable verificable |
|---|---|
| G0: inventario PCI | Implementado. Foto del arranque físico: 18 funciones, GTX 1050 Ti e I219-V detectadas. El detalle físico de BDF/BAR enviado al serial aún no se ha capturado |
| G1: acceso al dispositivo | En arranque físico separado, validar identificadores y mapeos MMIO, sin iniciar cómputo ni cambiar frecuencias |
| G2: memoria y transferencia | Buffer propiedad del servicio, ida/vuelta de un patrón conocido, límites y liberación comprobados |
| G3: motor y firmware | Arranque documentado de los componentes requeridos, cola de comandos, finalización y timeout observables |
| G4: primer cálculo | Suma de vectores dentro de ErlanOS; comparar todos los resultados con CPU en varios tamaños |
| G5: multiplicación para IA | Matmul con pruebas numéricas, tiempos con y sin transferencias, VRAM y frecuencias medidas |
| G6: modelo | Integrar operaciones y residencia de pesos; comparar calidad, primer token y tokens/s del mismo Qwen contra CPU |

G0 ya arrancó en el equipo físico y también identificó la futura interfaz de red
Ethernet. El siguiente hito es G1, que requiere otra prueba con el dispositivo
real bajo control de ErlanOS. No se puede asumir que la GPU virtual de QEMU representa una GP107,
ni que Windows y ErlanOS pueden programar simultáneamente la misma GPU.

Si faltan detalles después de estudiar estas fuentes, definir una observación
reproducible del controlador existente: qué operación se ejecutó, qué buffers y
comandos produjo y qué diferencia explica el resultado. Esa es la unidad de
ingeniería inversa propuesta; el objetivo no es reconstruir todo CUDA.

El primer criterio de éxito es cómputo correcto en nuestro kernel. La mejora de
rendimiento y el acceso a frecuencias de trabajo se evalúan por separado. No hay
todavía resultados que permitan prometer aceleración o uso de toda la GPU.
