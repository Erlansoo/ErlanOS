# Halo: espacio 3D nativo

Implementado el 17 de septiembre de 2026. FreeCAD y BMesh trabajan mediante sus APIs Python, sin MCP ni automatización de sus interfaces. Halo presenta ambos resultados en el mismo lienzo, independiente de una PCB.

## Ejecutar

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_halo_freecad.ps1
python -m halo.servidor --puerto 8767
```

Abrir http://127.0.0.1:8767/?pestana=espacio3d. Si el servidor necesita pcbnew, usar el Python incluido en KiCad, como las otras etapas de Halo.

FreeCAD portable 1.1.3 está en `build/halo-motores/freecad/`. El instalador fija versión y SHA256; la procedencia queda en `freecad-provenance.json`. BMesh se ejecuta en Blender 5.2 en segundo plano: no se extrajo como biblioteca independiente. Configuración alternativa: `HALO_FREECAD_PYTHON` y `HALO_BLENDER`.

## Qué está disponible

- CAD: cajas, cilindros, esferas, conos, perfiles rectangulares/circulares, extrusión, unión, corte, intersección, redondeo, chaflán y transformaciones.
- Mallas: cajas, icosferas, conos, biselado, subdivisión, suavizado y transformaciones mediante BMesh.
- Visor WebGL local: órbita, zoom, selección, encuadre, vista superior, aristas, sección visual y parámetros de operaciones anteriores.
- Proyectos persistentes en `data/halo3d`, con historial, deshacer/rehacer y reapertura.
- Archivos reales `.FCStd`, `.blend`, STEP para sólidos CAD y STL para ambos motores. Los STL no codifican unidades; las coordenadas se exportan en milímetros.
- Documentos y escena usan milímetros, Z arriba. CAD conserva representación exacta; el visor usa su triangulación.

## Contrato para agentes

El catálogo consultable está en `GET /api/espacio3d`. Consultar documento con `?project=ID`, trabajo con `?job=ID`. `POST /api/espacio3d` acepta `create`, `open`, `append`, `update`, `remove`, `undo`, `redo`, `note`, `cancel` y `demo`.

Crear:

```json
{"op":"create","name":"Mi diseño"}
```

Construir, usando el ID y revisión devueltos:

```json
{
  "op":"append", "project":"ID", "expected_revision":"REVISION",
  "operations":[
    {"id":"perfil","engine":"freecad","kind":"sketch_rectangle","params":{"width":60,"depth":40}},
    {"id":"pieza","engine":"freecad","kind":"extrude","inputs":["perfil"],"params":{"length":5}}
  ]
}
```

La respuesta devuelve un trabajo. Consultarlo hasta `complete`, `failed` o `cancelled`; después volver a leer el documento. `update` recibe `id` y `params` parciales y recalcula dependencias. IDs estables permiten consultar fuentes de una pieza. Cada modificación exige la revisión vigente, también después de deshacer: evita sobrescrituras accidentales.

Cliente directo para Codex/Claude:

```powershell
python -m halo.espacio3d --url http://127.0.0.1:8767
python -m halo.espacio3d --url http://127.0.0.1:8767 --request peticion.json
```

Escena: `/api/espacio3d/scene?project=ID&revision=REVISION`.
Archivos: `/api/espacio3d/artifact?project=ID&revision=REVISION&name=document.FCStd` (el documento enumera los nombres disponibles).

## Límites explícitos

Es un primer catálogo nativo, no todos los bancos de trabajo de FreeCAD. Los perfiles actuales tienen geometría fijada; sus dimensiones se cambian mediante la receta. No hay todavía ensamblajes, CAM, importación general STEP/PCB ni conversión automática de una malla artística a un sólido fabricable.

El campo «Guardar indicación» conserva notas `pending_agent`, selección y revisión. No hay un agente autónomo conectado a ese campo. Codex o Claude pueden consultar esas notas y ejecutar el contrato desde fuera. No presentar una nota guardada como trabajo realizado.

Los motores se ejecutan en procesos separados con tiempo y tamaño limitados; esto no constituye un sandbox de seguridad. La API sólo expone operaciones declarativas, no Python arbitrario. Una publicación aparece al terminar el trabajo; no se transmite cada vértice intermedio. La receta se recalcula completa por ahora.

## Validación

`python -m unittest halo.pruebas.test_espacio3d -v` prueba el contrato, motores reales, volumen analítico de una placa perforada, archivos nativos, cambio paramétrico, revisiones obsoletas, deshacer/rehacer, fallo de un redondeo imposible, cancelación y reapertura. Pasó con FreeCAD 1.1.3 y Blender 5.2.1 LTS.

Se verificó además el ejemplo mixto mediante la interfaz de Halo y se revisó visualmente el lienzo. Esto valida la integración, no certifica automáticamente cualquier pieza para fabricación.

## Actualización 2026-09-18: chat conectado

El campo de indicaciones ahora usa la cola de agentes por proyecto. Codex y Claude Code tienen adaptadores CLI, y las conversaciones externas pueden recibir observaciones mediante hooks oficiales. Se mantienen las operaciones nativas de FreeCAD y BMesh. La sección anterior sobre `pending_agent` describe sólo las notas antiguas: esas no se ejecutan automáticamente. Uso, activación, verificaciones y límites en [HALO-AGENTES.md](HALO-AGENTES.md).
