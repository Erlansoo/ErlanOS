# Trabajo en curso D1 y convivencia con NUBE

Codex implementó el encargo de persistencia de documentos, carpetas, explorador
con ratón y contextos de proyecto en `C:/Users/Erlan/SO-Erlan-D1-lab`. El 15/09
se integraron 35 archivos tras comparar hashes y fusionar el Makefile con las
reglas TCP/TLS actuales de Claude. Fuentes de red y pasarela sin modificaciones.
Copias previas y manifiesto: `build/workspace-d1/pre-integration/` e `integration.json`.
Guía de uso: `docs/WORKSPACE.md`; `make run` utiliza ahora el volumen persistente.

NUBE, pasarela, TCP/TLS, proveedores CLI y ranuras A/B quedan fuera de D1.
Para evitar colisiones se conservan SYS_NET_SEND=19, SYS_NET_RECV=20 y CAP_NET=9.
D1 propone SYS_STORAGE=21, SYS_POINTER=22, SYS_WORKSPACE_VIEW=23 y CAP_STORAGE=10;
slot 12 de `system` para almacenamiento. El modelo no recibe acceso a bloques.

El mecanismo inicial es VirtIO-blk para el disco de instalación de QEMU, con una
partición GPT de datos identificada. Sólo esa región queda accesible al servicio.
La futura actualización A/B requiere permisos propios y no debe escribir en la
partición de trabajos. AHCI/NVMe/USB físicos se integrarán detrás del mismo contrato
cuando sus controladores estén disponibles; D1 no promete soporte físico nuevo.

Puntos compartidos integrados: imports/llamadas en `kernel/src/main.c`,
syscalls añadidas en `kernel/src/syscall.e`, IRQ de ratón en `idt.e`, hooks de
composición alrededor de las syscalls de dibujo, rutas IPC en `services/system/main.e`, hooks de entrada
en `services/model/main.e` y módulos de Mind en Makefile. Se preservará el trabajo
concurrente mediante comparación con la base. La única coincidencia fue el Makefile,
fusionado sin conflictos. `console.e` no necesitó cambios en D1.

No es un encargo adicional a Claude ni cambia la prioridad de su trabajo. Es el
registro del reparto para revisar la integración.
