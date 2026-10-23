# Laboratorio de observación del controlador NVIDIA

Este kit prepara evidencia para el port a ErlanOS. Linux se usa como laboratorio;
el kernel del producto sigue siendo propio. El inventario no es una traza MMIO y
no demuestra todavía cómputo en ErlanOS.

## Inventario disponible

En una sesión de **Linux nativo con acceso a la GTX 1050 Ti**, desde este proyecto:

```bash
python3 scripts/gpu_lab/collect_linux.py --output build/gpu-lab
```

No necesita `sudo`. Cada ejecución crea una carpeta nueva con `inventory.json`
y su SHA256 en `manifest.json`. Registra versión de Linux, dispositivos PCI,
controlador asociado, recursos BAR ya publicados por Linux, métricas de NVIDIA,
configuración de trazado y herramientas disponibles. Los datos no salen del equipo.
Los comandos inexistentes o sin permiso se registran como no disponibles.

El recolector sólo consulta: no instala paquetes, monta sistemas de archivos,
activa trazadores, carga o descarga módulos, cambia relojes ni abre los archivos
PCI `config`, `rom` o `resourceN`. Tampoco lee `trace_pipe`, que podría consumir
eventos de una captura ajena. Escribe únicamente su directorio de resultados.
Los informes pueden incluir rutas de herramientas, versión del sistema e IDs de
dispositivos; se deben revisar antes de compartirlos voluntariamente.

`nvidia-smi` permite seleccionar campos y exportarlos a CSV. Las consultas de
estado y relojes no aplican cambios de configuración; valores no disponibles
quedan documentados. [Referencia oficial de NVIDIA-SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html).

## Requisitos para una captura activa posterior

La documentación del kernel especifica `CONFIG_MMIOTRACE=y`, arquitectura x86 o
x86_64 y activación antes de que el controlador establezca sus mapeos con
`ioremap`. MMIOTrace observa accesos del kernel; no cubre por sí solo accesos
originados en espacio de usuario ni todos los datos transferidos por DMA.
[Documentación del kernel Linux](https://docs.kernel.org/trace/mmiotrace.html).

Para la sesión física se necesita:

1. Linux arrancado directamente y una versión del controlador propietario que
   declare soporte para PCI `10de:1c82`. La presencia de un instalador descargado
   no acredita que funcione con ese kernel.
2. Una consola de control que sobreviva a la inicialización de la tarjeta: otra
   GPU o una consola remota previamente configurada. No iniciar una captura
   descargando el controlador de la única pantalla mientras se está trabajando.
3. Kernel con MMIOTrace disponible, suficiente almacenamiento y recuperación
   de arranque preparada. Activarlo puede desconectar temporalmente todos los
   núcleos CPU salvo uno; hay que comprobar pérdidas de eventos antes de usar
   una traza como evidencia. No se automatiza esa transición en este kit.
4. Un programa de cómputo pequeño y determinista: asignación, ida/vuelta de un
   patrón y suma de vectores por separado, con entradas, resultados CPU y tiempos
   guardados. El código GPU y el compilador del controlador deben soportar Pascal
   `sm_61` de forma explícita; el experimento de este kit usa el JIT del controlador.
5. Registro de versión/hash del controlador, GPU/BDF, configuración, programa
   exacto, marcadores por fase y resultado correcto/incorrecto. Una secuencia de
   escrituras sin esos datos no basta para reconstruir la operación.

La activación y el ajuste de buffers cambian el estado del sistema; por ello el
script de inventario no los ejecuta. Las limitaciones de CPU y eventos perdidos
están descritas por los desarrolladores del kernel. [Preparación y verificación de MMIOTrace](https://docs.kernel.org/trace/mmiotrace.html).

Nouveau documenta el uso del controlador propietario para estas observaciones y
la herramienta `demmio` de Envytools para anotar registros conocidos. Una traza
anotada aporta hipótesis que deben validarse; no se debe reproducir ciegamente
contra el dispositivo. [Guía de Nouveau](https://nouveau.freedesktop.org/MmioTrace.html).

Windows y una VM QEMU ordinaria no ofrecen aquí esa sesión Linux nativa. El test
`scripts/qemu_pci_test.py` valida el inventario PCI de ErlanOS contra dispositivos
**emulados**; no captura comandos de la GTX 1050 Ti física. No se ha iniciado una
captura activa, reemplazado Windows ni escrito una imagen en un disco físico.

## Experimento CUDA con el controlador instalado

El programa separado `cuda_probe.py` sí ejecuta operaciones en la GPU física
mediante la API pública del controlador instalado. Funciona en Windows y Linux;
no requiere instalar un toolkit porque el controlador compila el PTX del kit.

```bash
python3 scripts/gpu_lab/cuda_probe.py --output build/gpu-lab/cuda-reference
```

Ejecuta sumas de vectores y multiplicaciones pequeñas, compara todos los resultados
con CPU y guarda las entradas, salidas, PTX, CUBIN y llamadas a la API. Esas llamadas
son un registro a nivel de API, **no una traza MMIO ni de comandos de la GPU**.
Sirve como carga determinista para una captura Linux posterior y como referencia
numérica para el port; no significa que ErlanOS ya pueda ejecutar el CUBIN.

## Orden de análisis de una captura

Conservar el original y su hash; contrastar BDF/BAR con el inventario; separar
inicialización, asignación, transferencia y lanzamiento; anotar registros contra
las referencias públicas NVIDIA/Nouveau; proponer una operación mínima con
límites y timeout; finalmente verificarla en ErlanOS frente al resultado CPU.
El control de frecuencias y la aceleración de Qwen siguen siendo hitos separados.
