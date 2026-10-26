# GPU, internet y arranque físico

> **Actualización 26/10/2026:** la GPU de esta PC es ahora una **NVIDIA GeForce RTX 3090**
> (GA102, Ampere, PCI `10de:2204`), no la GTX 1050 Ti de esta evaluación. Los módulos abiertos
> de NVIDIA (dual MIT/GPL-2.0) sí cubren Ampere, lo que cambia la sección «Decisión». El
> inventario completo de hoy, los drivers que faltan, sus referencias abiertas con licencia y
> el orden de portado están en [DRIVERS-ESTUDIO.md](DRIVERS-ESTUDIO.md). Además, ErlanOS ya
> corre en VirtualBox en esta PC ([VIRTUALBOX.md](VIRTUALBOX.md)).

Evaluación del 14 de septiembre de 2026. El usuario confirmó conservar el kernel
propio. El arranque físico del diagnóstico G0 y la identificación PCI de GPU y
Ethernet están confirmados en una [prueba con fotografía](PRUEBA-FISICA-G0.md).
Las demás capacidades requieren sus propias pruebas; esto no certifica soporte
físico completo. Investigación específica de la GPU: [GPU-PASCAL.md](GPU-PASCAL.md).

## Equipo comprobado

Inventario de Windows mediante CIM y `nvidia-smi`, sin cambiar controladores:

| Componente | Detectado |
|---|---|
| CPU | Intel Core i7-6700, 4 núcleos / 8 hilos |
| RAM | 25.705.566.208 bytes, aproximadamente 24 GiB |
| Placa | ASUS SABERTOOTH Z170 S |
| GPU | NVIDIA GeForce GTX 1050 Ti, 4096 MiB, compute capability 6.1 |
| Controlador GPU de Windows | 581.57 |
| Ethernet | Intel I219-V, PCI 8086:15B8; deshabilitado en Windows al consultar |
| Wi-Fi | TP-Link High Power Wireless USB Adapter, USB 2357:0126; habilitado |

El identificador USB no se tomó como prueba de un chipset interno concreto.
No se comprobó si hay un cable Ethernet conectado. La GPU tenía 735 MiB ocupados
en Windows al consultar; esa cifra cambia y no mide consumo de ErlanOS.

## Qué está implementado

| Capacidad | Evidencia del proyecto | Falta para el objetivo |
|---|---|---|
| Arranque | UEFI + Limine y disco GPT/FAT32; diagnóstico G0 completado desde USB en esta placa | Validar servicios y funcionamiento prolongado en el equipo físico |
| Inventario PCI | G0 físico: 18 funciones; GTX 1050 Ti `10de:1c82` e I219-V `8086:15b8` detectadas | Capturar detalle físico de BDF/BAR y validar el acceso a cada dispositivo |
| Pantalla | Framebuffer proporcionado por Limine; diagnóstico visible en el monitor físico | Esto no proporciona cómputo GPU |
| Inferencia | CPU, Q8_0/F16/F32, especialización AVX2 | Backend GPU, gestión de VRAM y controlador |
| CPU multinúcleo | Inicialización y scheduler de un procesador; no arranque SMP | Inicializar otros núcleos y paralelizar operaciones |
| Teclado | PS/2 | USB/xHCI/HID para entrada USB independiente del firmware |
| Red | Driver e1000 (QEMU) + ARP/IPv4/UDP/DHCP/DNS en Erlan; DNS real verificado en QEMU | Driver I219-V físico (familia e1000e: init PHY/PCH y MSI), TCP, TLS; servicio Ring 3 |
| Almacenamiento | Limine carga servicios y GGUF como módulos | Controlador de disco, filesystem y persistencia en ejecución |
| Instalación | Generador de imagen de prueba | Instalador, recuperación y validación en hardware real |

Se revisaron `kernel/src/main.c`, los módulos de `kernel/src/`,
`services/model/platform.h`, `services/model/gguf.e` y el Makefile.
La imagen actual no se considera lista para sustituir un sistema de uso diario.

## Decisión: conservar el kernel propio

Los [módulos abiertos de NVIDIA](https://github.com/NVIDIA/open-gpu-kernel-modules)
admiten Turing y posteriores. La GTX 1050 Ti es anterior y no queda cubierta.
Además, esos módulos están integrados con Linux: no son un controlador que se
pueda cargar directamente en el microkernel ErlanOS. Instalar el toolkit CUDA
tampoco aporta por sí solo esa integración.

Se mantiene el kernel propio y se desarrolla su infraestructura de dispositivos,
red e instalación. Linux sirve como referencia y banco de pruebas. Nouveau sí
incluye soporte para GP107; la limitación de los módulos abiertos de NVIDIA no
significa que no exista código abierto para esta tarjeta. Se estudiarán las
dependencias mínimas para adaptarlas a ErlanOS. Todavía no hay una ruta validada
para aprovechar toda su capacidad en nuestro kernel.

Un servicio externo en Windows/Linux también serviría como banco de pruebas,
pero sería inferencia asistida por otro sistema, no soporte GPU nativo de ErlanOS.

## Tamaño de modelo e internet

Los 24 GiB de RAM y los 4 GiB de VRAM son recursos distintos. Como estimación
teórica, 9.000 millones de pesos a 4 bits requieren 4.500 millones de bytes
(4,19 GiB) sólo para pesos, sin escalas, estado del modelo ni buffers. Ese caso
no cabe completo en 4 GiB de VRAM. Cuantizaciones menores y distribución entre
CPU/GPU cambian el cálculo, con compromisos de calidad y velocidad.

[llama.cpp admite inferencia híbrida CPU/GPU](https://github.com/ggml-org/llama.cpp),
pero esa capacidad aún no existe en el runtime Erlan. Un Qwen de 2B o 4B
cuantizado sería un candidato para medir primero con un backend compatible;
no se presupone que quepa ni se promete una velocidad antes del benchmark.
El runtime nativo actual tampoco acepta Q4: necesita implementación y pruebas.

Conectarse a internet permite obtener fuentes actuales; no aumenta la VRAM ni
la capacidad de cálculo local. Usar un modelo mayor en un servidor remoto es
otra función, con dependencia de servicio y posible coste.

## Orden propuesto para el kernel propio

1. **Base de hardware:** arranque de diagnóstico desde USB e identificación PCI
   confirmados en esta placa. Siguen pendientes ACPI, validación física de
   interrupciones y recursos DMA/MMIO, entrada y registro persistente de fallos.
   La prueba G0 no instala el sistema en disco.
2. **Red reproducible:** hecho en su primera mitad (N0): NIC e1000 en QEMU, DHCP y DNS
   reales; falta TCP y mover la pila a un servicio aislado. Un controlador virtual no habilita la I219-V.
3. **Red física:** controlador específico para I219-V y prueba por cable.
   Wi-Fi USB requiere además USB y soporte del adaptador; es trabajo separado.
4. **Investigación web:** TLS con certificados, reloj y entropía adecuados;
   servicio de búsqueda/lectura con límites, cancelación, extracción de texto
   y URLs de procedencia. Primero una petición explícita `/buscar`; después
   integrar la decisión de buscar en el planner y su capability de red.
   Tratar el contenido recuperado como datos y probar que no concede permisos.
   La ventana actual de 512 tokens necesita ampliación o selección de fragmentos.
5. **Rendimiento medible:** SMP y paralelización CPU, Q4 y evaluación numérica;
   prueba de viabilidad GPU con resultados comparados contra CPU antes de
   incorporar inferencia completa. Detectar una GPU o dibujar no cuenta como
   aceleración del modelo.
6. **Instalación:** almacenamiento persistente, recuperación y prueba prolongada
   del conjunto. Preparar el instalador cuando las funciones requeridas estén
   verificadas en el hardware de destino.

## Criterios para afirmar que funciona

- GPU: resultado numérico contrastado con CPU, consumo de VRAM medido y mejora
  de tiempo hasta primer token y tokens/segundo en el mismo modelo/contexto.
- Internet: búsqueda y lectura HTTPS reales, respuesta con fuentes verificables,
  cancelación y manejo de errores de red o certificado.
- Hardware físico: arranque, teclado, red, memoria y persistencia probados en
  esta placa; resultados guardados y recuperación comprobada.

La decisión de conservar el kernel está confirmada. G0 físico y el prototipo de
red en QEMU son avances comprobados; cómputo GPU nativo, internet en la placa e
instalación persistente todavía no están validados.
