# D1 — Mis archivos y conversaciones de proyecto

El botón de carpeta, dentro de un cuarto de círculo de cobre en la esquina inferior
derecha, abre el explorador nativo. Conserva el lienzo café y los tres puntos.
Un espacio nuevo empieza vacío: no muestra discos físicos, letras de unidad ni
carpetas inventadas. Un clic selecciona; doble clic abre carpetas y documentos.

## Arrancar con persistencia

Desde PowerShell, en el repositorio:

```powershell
.\scripts\run_workspace.ps1
```

O desde Git Bash: `source scripts/env.sh && make run`.
El modelo predeterminado sigue siendo Qwen3.5-2B Q8_0; no se descarga otro modelo.
La imagen persistente es `data/workspace.img`. Al recompilar se actualiza el arranque
conservando exactamente su partición de trabajos. Una exclusión de acceso impide
abrirla simultáneamente desde dos sesiones de este lanzador. `make clean` no borra
`data/`. Para una copia de seguridad, cierra la sesión y copia `data/workspace.img`.

Los lanzadores antiguos de pruebas y `gdb` crean imágenes independientes. Usa el
lanzador D1 para conservar tus documentos. No se ha grabado ningún USB ni disco físico.

## Carpetas y documentos

- **+ Carpeta**: escribe el nombre y pulsa Enter. Escape cancela.
- **Guardar**: guarda el documento actual; la primera vez pide su nombre.
- Doble clic en un documento lo abre en el visor principal con su texto, fuente,
  tamaño de página y márgenes. Si hay cambios sin guardar, puedes guardarlos,
  cancelar o elegir explícitamente abrir sin guardarlos.
- **Inicio** vuelve a la raíz; la flecha izquierda sube un nivel. Las flechas del
  pie permiten recorrer más elementos. No se enumeran otros dispositivos.

También se pueden pedir operaciones mediante texto:

```text
crea una carpeta llamada Documentos
abre la carpeta Documentos
crea un documento
/doc titulo Informe de energia
/doc texto Este es el primer parrafo.
Pon arriba 2 cm, abajo 3, izquierda 2.5 y derecha 4
guarda el documento como Informe
/guardar
/abrir Informe
/archivos
```

`/guardar Nombre` y `/doc guardar Nombre` crean una copia con ese nombre;
`/guardar` actualiza el documento abierto. Un nombre ya ocupado no se sobrescribe
silenciosamente. Los nombres son componentes simples, de hasta 80 bytes UTF-8,
sin `/`, `\`, `:` ni espacios iniciales/finales. No hay renombrado ni borrado aún.

Guardar conserva el formato nativo ELD1 dentro de ErlanOS. `/doc exportar` mantiene
la exportación D0: el lanzador convierte la instantánea a PDF, DOCX y ODT si el
anfitrión tiene las bibliotecas de exportación. Guardar no depende de esas bibliotecas.
La conversión externa sigue siendo un puente declarado, no una exportación nativa.

## Proyectos y chat

**+ Proyecto** crea una carpeta de proyecto y pregunta si quieres continuar en su
chat. Confirmar cambia el contexto y la carpeta de trabajo; cancelar mantiene el
chat actual. Las conversaciones general y de cada proyecto tienen registros separados.

```text
/proyecto nuevo Energia Solar
/proyecto abrir Energia Solar
/general
```

Desde el chat general, mencionar el nombre completo de un proyecto existente
propone abrir su conversación. La petición queda pendiente y sólo se envía allí
después de aceptar. Cancelar la descarta. Si hay varios nombres coincidentes,
se pide elegir un proyecto. El reconocimiento actual es por nombre, sin búsqueda
semántica ni embeddings: no adivina referencias vagas como «lo de ayer».

Al cambiar, se muestran los mensajes recientes y se reconstruye el contexto local
desde roles guardados, reiniciando tanto KV como el estado recurrente de Qwen.
Se conserva hasta 32 KiB de historial por conversación; el contexto del modelo
recibe el tramo reciente que cabe, dejando espacio para el siguiente turno.
Los mensajes y respuestas del chat se guardan; las órdenes de edición usan su
propio protocolo y no se convierten en turnos de conversación.

El servicio local ya usa este contrato. La pasarela de Claude/Codex deberá vincular
una sesión remota explícita a cada ID de proyecto. D1 no implementa ni modifica NUBE,
TCP/TLS, autenticación de proveedores ni actualizaciones A/B.

## Mecanismo y límites actuales

- Kernel en Erlan: VirtIO-blk legacy, PS/2 y composición del explorador.
- Broker `system`: journal, carpetas, guardado y política de proyectos. El modelo
  sólo recibe operaciones por IPC; no recibe una capability de almacenamiento.
- `SYS_STORAGE=21`, `SYS_POINTER=22`, `SYS_WORKSPACE_VIEW=23`, `CAP_STORAGE=10`.
  Almacenamiento sólo en slot 12 de `system`, restringido a la partición identificada.
  Se reservan 19/20 y CAP_NET=9 para el trabajo concurrente de red.
- Una partición de 128 MiB en el mismo disco de arranque de QEMU. Hasta 128 objetos,
  incluidos registros internos de chat. Documento: los límites D0, 16 KiB de texto.
- El journal agrega versiones: escribe y vacía datos antes de confirmar la cabecera.
  Ante un fallo ambiguo queda en sólo lectura; al montar conserva el prefijo válido.
  No hay compactación, reparación automática ni almacenamiento ilimitado.
- Este controlador es para **VirtIO en QEMU**. El guardado físico AHCI/NVMe/USB y
  los ratones USB requieren otros controladores. Su ausencia se muestra; no se finge
  que los cambios se guardaron. No constituye un instalador físico.

## Verificación

`scripts/test_workspace_host.py`: motor real, importación exacta, rechazos sin perder
el documento, separación de chats y fallos en cada fase de un guardado.

`scripts/test_workspace_disk.py`: reconstrucción del arranque con datos intactos,
identidad GPT, exclusión de doble escritura y rechazo de imágenes ajenas.

`scripts/test_pointer_host.py`: paquetes PS/2, botones, movimiento firmado, bordes,
pitch y restauración exacta del cursor.

`scripts/qemu_workspace_test.py`: carpeta mediante ratón, guardado, doble clic,
reinicio y comparación de contenido y márgenes. `scripts/qemu_projects_test.py`:
modelo real, confirmación de proyecto, reanudación y separación tras reiniciar.

Las pruebas rápidas usan SmolLM2 para comprobar mecanismos; no evalúan la calidad
de sus respuestas. El lanzador de uso mantiene Qwen3.5-2B.

Aceptación del 15/09/2026: compilación del árbol integrado con
`make BUILD=build/d1-integrated all` y prueba nativa completa aprobadas.
El resultado está en `build/workspace-test/20260915-102616-4tndrg3g/result.json`;
la captura `06-integrado-archivos.png` muestra el documento guardado dentro del
proyecto y el explorador. También pasó el flujo de documentos con Qwen3.5-2B
en el laboratorio aislado, además de las pruebas de persistencia tras reiniciar.
