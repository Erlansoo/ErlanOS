# Documentos D0

El primer editor experimental de ErlanOS vive sobre el lienzo café. El contenido,
la configuración y las revisiones son datos del servicio nativo `system`, escritos
en Erlan. Tanto el teclado como las órdenes de la IA llegan a ese mismo motor.
No se genera un programa Python para crear o modificar el documento.

## Cómo sabe la IA cuándo usarlo

La intención selecciona una capacidad de trabajo. En D0, un adaptador nativo
reconoce un conjunto acotado de frases y elige operaciones estructuradas. Por ejemplo:

| Escribes | Lo que ejecuta el sistema |
|---|---|
| `crea un documento` | Crea un documento con la plantilla común |
| `Redacta un informe breve sobre energia solar` | Pide un borrador al modelo local y reemplaza el cuerpo de forma atómica |
| `Pon arriba 2 cm, abajo 3, izquierda 2,5 y derecha 4` | Cambia cuatro propiedades numéricas del documento activo |
| `Usa margenes espejo` | Intercambia interior y exterior en las páginas pares |
| `Usa fuente serif 12` | Cambia familia y tamaño; vuelve a calcular las páginas |

El modelo redacta el cuerpo; las medidas no dependen de que el modelo haga una
cuenta correcta ni de que encuentre un botón. El motor valida la petición y responde
con un recibo, una revisión y el estado resultante. La vista lee ese mismo estado.

Este reconocimiento todavía **no es un planificador universal de lenguaje natural**.
Las configuraciones requieren un verbo como `pon`, `usa`, `cambia`, `ajusta` o
`configura`, y un documento abierto. Una pregunta sobre una fuente de energía no
activa el cambio de tipografía. Márgenes, espejo, fuente y papel se piden en líneas
separadas; una combinación ambigua se rechaza sin aplicar sólo una parte.

El catálogo [document-api.json](document-api.json) describe las operaciones reales.
Sirve como contrato para un futuro planificador con salida estructurada. D0 no carga
ese JSON dentro del modelo ni descubre programas instalados dinámicamente.

## Abrir y probar

Desde PowerShell, en la raíz del proyecto:

```powershell
.\scripts\run_document_editor.ps1
```

Compila, abre una ventana QEMU independiente con Qwen3.5-2B y WHPX, y muestra el
documento cuando el modelo está listo. El primer arranque y la carga del modelo
tardan. Se conservan los tres puntos y el fondo; la hoja ocupa el área de trabajo.
El panel derecho muestra las propiedades actuales. Para usar el modelo pequeño en
pruebas de edición: `-Model models/smollm2-135m-instruct-q8_0.gguf`.

Secuencia de ejemplo, una línea cada vez:

```text
/doc titulo Informe de energia solar
Redacta un informe breve sobre los beneficios de la energia solar
Pon arriba 2 cm, abajo 3, izquierda 2.5 y derecha 4
Usa margenes espejo
Usa fuente serif 12
/doc editar
```

En edición, escribe directamente. Enter añade un párrafo; Backspace borra; izquierda
y derecha mueven el cursor; Home y End van al inicio/final del cuerpo. Esc vuelve a
las instrucciones. La edición no hace inferencia. `/doc ia ...` conserva título y
configuración, y sustituye el cuerpo; `/doc deshacer` permite recuperarlo.

También funciona dentro de `make run`, escribiendo `/doc` para mostrar la hoja,
pero el guardado automático en Windows requiere el lanzador anterior.

## Comandos de D0

| Comando | Efecto |
|---|---|
| `/doc` o `/doc estado` | Muestra el documento y sus propiedades |
| `/doc nuevo` | Documento nuevo A4, márgenes 2,5 cm, DejaVu Serif 11 pt |
| `/doc titulo <texto>` | Título, máximo 96 bytes UTF-8 |
| `/doc texto <texto>` | Añade un párrafo, máximo 184 bytes por orden |
| `/doc ia <peticion>` | Redacción local del cuerpo; Esc cancela |
| `/doc editar` | Activa la escritura con teclado |
| `/doc margenes 2 3 2.5 4` | Arriba, abajo, izquierda/interior, derecha/exterior, en cm |
| `/doc margenes 25 mm` | Un mismo margen en los cuatro lados |
| `/doc espejo` / `/doc normal` | Activa/desactiva márgenes espejo |
| `/doc papel a4` | A4; también `carta` o `a5` |
| `/doc fuente sans 11.5` | DejaVu Sans; también `serif`; tamaños de 8 a 24 pt |
| `/doc pagina 2` | Cambia la página visible |
| `/doc salto` | Inserta un salto de página en el cursor |
| `/doc deshacer` | Recupera uno de los cuatro estados anteriores |
| `/doc exportar` | Emite una instantánea íntegra por serial |
| `/doc cerrar` | Cierra la vista; conserva el documento en RAM |
| `/doc ayuda` | Muestra los comandos disponibles |

Se admite coma o punto decimal. Las unidades de margen admitidas son cm y mm;
omitir la unidad significa cm. Los valores internos son micrómetros enteros. La
fuente usa décimas de punto y admite incrementos de 0,5 pt, compatibles con Word.
No se aceptan márgenes que dejen menos de 30 mm de
ancho o 40 mm de alto para texto.

## Guardar y exportar

**D0 todavía no tiene almacenamiento de documentos dentro del SO.** El documento
vive en RAM y se pierde al reiniciar. El lanzador escucha `/doc exportar` y guarda
una copia en `output/documentos/<sesion>/documento-001.*`, después `002`, etc. No
sobrescribe las exportaciones anteriores ni necesita que el modelo elija rutas.

Se obtienen cinco archivos:

- `.eld`: instantánea ELD1 exacta del servicio nativo.
- `.json`: texto, medidas, revisión y metadatos para inspección.
- `.pdf`: formato fijo, con la posición de las líneas y medidas del motor nativo.
- `.docx`: texto editable, tamaño de papel, fuentes, márgenes y modo espejo.
- `.odt`: las mismas propiedades en OpenDocument.

La conversión PDF/DOCX/ODT es un **puente temporal en el anfitrión**, escrito en
Python. No forma parte de la ruta de edición de ErlanOS. Sustituirlo por escritores
PDF/ZIP nativos y un servicio de archivos es trabajo pendiente. Word y LibreOffice
pueden redistribuir líneas de DOCX/ODT; esos formatos no prometen identidad de píxeles
con la vista. Los nombres de las fuentes se conservan: están en `assets/fonts/`,
con su licencia. El PDF incorpora la fuente.

Para convertir un log ya capturado:

```text
python scripts/export_document.py RUTA/serial.log --out output/documentos/prueba
```

Ese Python necesita `reportlab` y `python-docx`. El lanzador PowerShell usa el
runtime incluido con Codex si está disponible. No requiere Word ni LibreOffice
para exportar. Las tramas incompletas no se exportan y las corruptas se rechazan.
FNV-1a detecta errores de transmisión; no es una firma criptográfica.

## Arquitectura y contrato

`mind/document_client.e` interpreta la intención y envía IPC por los endpoints M4.
`services/system/document.e` posee texto, estilos, cursor, cuatro copias de deshacer,
transacciones de borrador y cálculo de líneas. Se integra en el proceso aislado
`system`; no se crea otro proceso ni se amplían los permisos del modelo.

`kernel/src/document_view.e` dibuja la página mediante la syscall 18. Exige
`CAP_CONSOLE/RIGHT_PRESENT`, que pertenece al broker. El modelo conserva su consola
sin ese derecho. La vista recibe un paquete acotado, lo copia y valida completo
antes de pintar. El encabezado con los tres puntos queda fuera de la superficie.
Un fallo de validación, conflicto de revisión o rechazo del visor conserva el
estado del documento. Un recibo exitoso confirma la operación, no la veracidad del
texto redactado por el modelo.

El protocolo DOC1 es distinto de AI1/M4:

- Petición: 9 enteros u64 little-endian, más hasta 184 bytes de datos: magic,
  request_id, opcode, expected_revision, a, b, c, d, payload_length.
- Respuesta: 256 bytes. Cabecera: magic, request_id, opcode, status, receipt;
  después la revisión y propiedades. En byte 160: páginas; 168: deshacer;
  176: longitud de lectura; 184: hasta 72 bytes de texto/título.
- Los IDs crecen, y las mutaciones exigen la revisión vigente. Consultar no crea
  otra revisión; cambiar cursor/página tampoco. La subida por fragmentos exige
  offsets consecutivos y valida UTF-8 al confirmar, admitiendo cortes entre bytes.
- Estado ELD1: 16 u64, título de hasta 96 bytes desde el byte 128 y cuerpo desde el
  224. El catálogo detalla los campos y operaciones.

## Límites de esta primera versión

Un documento, 16 KiB de cuerpo, hasta 32 páginas y 1.024 líneas. Cuatro cambios de
deshacer, incluidos los cambios de teclado, sin rehacer. Sin selección con ratón,
zoom, tablas, imágenes, estilos por fragmento, portapapeles, importación o reapertura
de ELD1. El título usa 18 pt y el cuerpo usa el tamaño configurado. La vista encaja
la hoja completa, por lo que el texto se ve pequeño; el zoom es una mejora posterior.

El estado guarda UTF-8; la vista gráfica incluye Latin-1, suficiente para español.
Otros caracteres se muestran como `?`. El teclado físico sigue el mapa PS/2 existente
del proyecto. No hay corrector ni investigación en internet. Qwen genera hasta 256
tokens: puede quedar una frase incompleta y se avisa del límite. Esc conserva el
cuerpo anterior si la generación se cancela.

## Validación reproducible

```bash
source scripts/env.sh
make all
python scripts/test_document_host.py
python scripts/test_m4_kernel_host.py
python scripts/test_input_host.py
python scripts/qemu_document_test.py --accel whpx --model models/smollm2-135m-instruct-q8_0.gguf
python scripts/qemu_document_test.py --accel whpx --draft --command-timeout 480 --timeout 900
python scripts/qemu_ui_test.py --accel whpx --model models/smollm2-135m-instruct-q8_0.gguf
```

Los tests crean VMs y logs privados. El test de host ejecuta los módulos Erlan
reales: parser de intención, motor y framebuffer con stride no trivial. Incluye
medidas, Unicode, deshacer, IDs repetidos, revisiones obsoletas, subida de texto,
rechazo del visor y comandos ambiguos. QEMU comprueba el teclado, la hoja y el
recorrido de inferencia. `test_document_exports.py` verifica el texto y la geometría
del PDF y los contenedores DOCX/ODT a partir de una instantánea real.
