# Halo: plan de trabajo

Estado: aprobado por el usuario el 19 de septiembre de 2026. Este documento es la referencia de
qué se hace, en qué orden, y cómo se sabe que cada etapa está terminada.

## 1. Propósito

Halo es el programa donde la IA trabaja a la vista del usuario: diseña esquemáticos y placas en
KiCad, simula, fabrica, modela carcasas en 3D y documenta, y el usuario corrige a mano lo que
quiere. Este plan lo lleva de una vista por proyecto a un programa con pestañas, cada una un
proyecto con sus etapas, su documentación y sus conversaciones con Claude Code.

La mente maestra sigue siendo la terminal de Claude Code de esta máquina; los chats del Halo son
sesiones oficiales de Claude Code que esa terminal puede retomar.

## 2. Principios

1. **La IA manda, el motor garantiza, KiCad juzga.** Nada se da por bueno sin la verificación del
   programa oficial: ERC, netlist, DRC, paridad.
2. **Programas de código abierto** y sus formatos nativos: KiCad, ngspice, FreeCAD, Blender,
   MiKTeX, pandoc. Nada de Word.
3. **Sin trabajo en reposo.** Con el Halo abierto y nadie trabajando, el consumo de CPU debe ser
   cero. Los motores arrancan cuando se necesitan y se cierran al terminar.
4. **Sólo en esta máquina.** El servidor escucha en 127.0.0.1 y rechaza cualquier petición que no
   venga de su propia interfaz.
5. **Cada cambio deja rastro y se puede deshacer**, venga de la IA o del usuario.
6. **Lo que no está probado no está hecho.** Cada etapa termina con pruebas automáticas y una
   comprobación en vivo en el Halo, y ambas quedan escritas en `docs/HALO.md`.

## 3. Punto de partida (medido el 19/09/2026)

| Componente | Memoria | CPU en reposo |
|---|---|---|
| Servidor del Halo (Python, biblioteca estándar) | 24 MB | 7 % de un núcleo |
| Ventana del Halo (Edge en modo aplicación) | 200–300 MB | ~0 |
| KiCad, FreeCAD, Blender, ngspice | cientos de MB cada uno, sólo mientras trabajan | 100 % de uno o varios núcleos mientras calculan |

Equipo: i7-6700 (4 núcleos, 8 hilos), 24 GB de RAM, GeForce GTX 1050 Ti con 4 GB.

Decisiones que se tomaron con esos datos:

- **No se reescribe en C++.** El peso está en los motores, que serían los mismos; el servidor es
  pequeño. Una reescritura costaría meses y sólo cambiaría la ventana. Si el dibujo de placas
  grandes se vuelve lento, el paso es WebGL, no otro lenguaje.
- **La GPU dibuja; la CPU calcula.** El 3D ya va por la GPU (three.js). El 2D pasará a WebGL
  cuando haga falta. Los motores de cálculo no se benefician de la GPU sin reescribirlos.
- **Codex queda fuera.** Las conversaciones del Halo serán sólo de Claude Code.

## 4. Hoja de ruta

Cinco etapas, en este orden. Cada una se entrega, se prueba y se documenta antes de empezar la
siguiente. Las dos primeras son la base de las demás.

### Etapa 1. Servidor por eventos y motores bajo demanda

**Objetivo.** Que el Halo abierto y en reposo no consuma CPU, y que nada pese si no se usa.

**Alcance.**
- El vigilante de archivos deja de sondear cada 0,12 s: el sistema avisa cuando cambia un
  archivo del proyecto o del estado (`ReadDirectoryChangesW` vía ctypes, sin dependencias) y
  sólo entonces se relee. Si el sistema no avisa, sondeo lento de respaldo.
- Los servicios que viven dentro del servidor (simulación, 3D, agentes) avisan a la interfaz por
  SSE con un contador por servicio; la interfaz deja de preguntar cada segundo. Sólo mientras un
  trabajo está en marcha hay una consulta acotada de seguimiento.
- El despachador de agentes espera a que lo despierten en vez de consultar SQLite tres veces por
  segundo. Buscar ejecutables (Claude, FreeCAD) se recuerda un minuto.
- Los motores externos siguen siendo procesos por trabajo: se lanzan al pedirlos y terminan solos.

**Entregables.** `halo/vigia.py`, cambios en `servidor.py`, los tres servicios y sus vistas,
`halo/pruebas/test_vigia.py`.

**Aceptación.**
- CPU del servidor en reposo, con la ventana abierta en la pestaña PCB: menos de 0,5 % de un núcleo
  (antes 7 %).
- Un cambio en el esquemático o en la placa aparece en el Halo en menos de medio segundo.
- Una simulación, un trabajo 3D o un encargo a un agente se ven avanzar sin que la interfaz sondee
  en reposo.
- Todas las suites siguen pasando.

**Riesgos.** Una escritura larga de KiCad puede leerse a medias: se reintenta al instante. Si
`ReadDirectoryChangesW` fallara en alguna carpeta, ese vigilante vuelve al sondeo lento.

### Etapa 2. Proyectos y pestañas

**Objetivo.** Varios proyectos abiertos a la vez, cada uno en su pestaña, y el Halo como programa.

**Alcance.**
- Estado por proyecto: `actual.json` deja de ser único; cada proyecto tiene el suyo y el servidor
  sirve N proyectos. Cada pestaña fija su proyecto en la dirección (`?project=`), como ya hace el
  espacio 3D.
- Archivo de proyecto (`proyecto.json`) con nombre, carpeta, etapas y su estado, evidencias y
  conversaciones.
- Barra de pestañas propia del Halo, arriba, con su estilo; no la de Chrome. Nueva pestaña,
  cerrar, reordenar, recordar las abiertas.
- `Halo.exe`: un lanzador que arranca el servidor si no está y abre la ventana propia, sin barra
  de direcciones. En Windows la ventana usa el mismo motor que Edge (WebView2); no ahorra memoria
  pero se comporta como un programa.
- Motores por proyecto, bajo demanda; ningún motor queda vivo por un proyecto en el que no se
  trabaja.

**Aceptación.**
- Tres proyectos abiertos en tres pestañas, cada uno con su esquemático, su placa y su bitácora,
  sin mezclarse.
- Cerrar y volver a abrir el Halo recupera las pestañas.
- Con tres pestañas abiertas y nadie trabajando, ningún proceso de KiCad, FreeCAD ni Blender vivo.

### Etapa 3. Chat de Claude Code por pestaña

**Objetivo.** Conversar con Claude Code desde el Halo, por proyecto, con varias sesiones por
pestaña, sin modificar el binario oficial.

**Alcance.**
- Panel lateral izquierdo, estilo de mensajería, con los colores del Halo.
- Cada chat es una sesión oficial de Claude Code lanzada en modo de flujo (`--print`,
  `--input-format stream-json`, `--output-format stream-json`, `--include-partial-messages`):
  burbujas, herramientas visibles, permisos como botones que responde el Halo, modelo y esfuerzo
  como opciones, reanudar y bifurcar sesiones. Una sola sesión activa a la vez por pestaña.
- Modo consola para lo que sólo existe interactivo (`/model`, etc.): el botón «Consola» abre la
  misma sesión en una ventana de Claude Code; el Halo la marca ocupada hasta que se cierra. (La
  interfaz de texto de Claude Code no se incrusta en el panel: necesitaría un emulador de terminal.)
- Botón «Remoto»: abre esa sesión con `--remote-control` en esta PC para seguirla desde el
  celular; el Halo la marca como remota y no le envía turnos hasta que vuelve.
- Esta terminal puede retomar cualquier sesión del Halo por su identificador.

**Aceptación.**
- Un proyecto con tres chats; sólo uno activo; los otros dos se reanudan con su historial.
- Un permiso pedido por Claude Code se responde desde el Halo y la herramienta corre.
- Una sesión pasa a remoto, se continúa desde el celular y, al volver, el Halo muestra lo que pasó.
- Antes de construir: comprobar que una sesión creada en modo silencioso se puede retomar con
  Remote Control; si no, crearlas ya en ese modo. **Comprobado el 20/09/2026: sí se puede**
  (`claude --resume <id> --remote-control "<nombre>"` sobre una sesión creada por el Halo con `-p`).

**Límite.** Política de Anthropic: la suscripción sólo se usa con el binario oficial sin
modificar. Aquí se usa tal cual; el Halo sólo le habla por su entrada y salida.

### Etapa 4. Documentación y etapas del proyecto

**Objetivo.** Que cada proyecto tenga su documentación viva y que la IA guíe por etapas.

**Alcance.**
- Markdown renderizado en el Halo (sin dependencias externas) y LaTeX compilado a PDF con MiKTeX,
  visto dentro del Halo. Pandoc para convertir entre formatos. LibreOffice, cuando haga falta,
  para formatos de oficina.
- Etapas como guía, no como regla: documentación → esquemático y simulación → PCB → 3D de la
  carcasa con el modelo de la placa. La IA propone la etapa siguiente; el proyecto puede
  saltársela. Cada etapa devuelve observaciones a la documentación (resultados de ERC, DRC,
  simulación, fabricación).
- Plantillas de documento por tipo de proyecto.

**Aceptación.**
- Un proyecto documentado de punta a punta: memoria en Markdown, informe en LaTeX con el
  esquemático, la placa y las medidas de simulación, todo generado desde el Halo.
- Cambiar el esquemático marca como desactualizadas las secciones del documento que dependen de él.

### Etapa 5. Dibujo 2D en WebGL

**Objetivo.** Placas y esquemáticos grandes fluidos.

**Alcance.** Sustituir el SVG del lienzo por un dibujo en WebGL cuando el modelo supere un umbral
(por ejemplo, más de 1000 pistas), conservando la edición a mano. Índice espacial para la
selección.

**Aceptación.** Una placa grande de prueba (unas 2000 pistas) se desplaza y amplía a 60 cuadros por
segundo en la GTX 1050 Ti, y arrastrar una huella sigue funcionando igual.

## 5. Dependencias y orden

```
Etapa 1 (base ligera) → Etapa 2 (proyectos y pestañas) → Etapa 3 (chat por pestaña)
                                                       └→ Etapa 4 (documentación y etapas)
Etapa 5 (WebGL) es independiente; se hace cuando el rendimiento lo pida.
```

## 6. Fuera de alcance por ahora

- Reescritura en C++ o en otro lenguaje.
- Cálculo en GPU.
- Codex.
- Exponer el Halo fuera de esta máquina (una VPN sería otro proyecto).

## 7. Seguimiento

| Etapa | Estado | Fecha | Evidencia |
|---|---|---|---|
| 1. Servidor por eventos y motores bajo demanda | hecha: 7 % → 0,3 % de un núcleo en reposo | 19/09/2026 | `docs/HALO.md` («Servidor por eventos»), `halo/pruebas/test_vigia.py` |
| 2. Proyectos y pestañas | hecha: estado por proyecto, pestañas persistentes, `Halo.exe` | 19/09/2026 | `docs/HALO.md` («Proyectos y pestañas»), `halo/pruebas/test_pestanas.py`, `docs/evidence/halo-pestanas.png` |
| 3. Chat de Claude Code por pestaña | hecha y comprobada con el binario real: turno en vivo, permiso concedido desde los botones, y sesión silenciosa retomada con Remote Control | 19–20/09/2026 | `docs/HALO.md` («Chat de Claude Code por pestaña»), `halo/pruebas/test_chat.py`, `docs/evidence/halo-chat.png`, `docs/evidence/halo-chat-permiso.png` |
| 4. Documentación y etapas | hecha: memoria (Markdown) e informe (LaTeX → PDF con MiKTeX) generados desde el proyecto, secciones con la revisión de su fuente que se marcan desactualizadas, guía de etapas, pandoc para DOCX | 20/09/2026 | `docs/HALO.md` («Documentación viva»), `halo/pruebas/test_documentos.py`, `docs/evidence/halo-documentos.png` |
| 5. Dibujo 2D en WebGL | pendiente | | |
