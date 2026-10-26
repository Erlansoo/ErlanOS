# Halo

Programa propio donde se ve, en vivo y sobre el fondo café, el trabajo que hace la IA.
Esquemáticos y placas de KiCad construidos con control nativo, sin MCP.

Incluye revisiones de proyecto, bloqueo entre procesos, verificación sin falsos
positivos, símbolos multipartes y consultas `/api/contexto` y `/api/capacidades`.
La página y el servidor deben recargarse después de actualizar el código.

```
Halo.exe                                    # arranca el servidor si hace falta y abre la ventana del Halo
python -m halo.servidor                     # sólo el servidor local en 127.0.0.1:8765
powershell -File halo\lanzador\construir.ps1  # vuelve a compilar Halo.exe y su icono (C# de Windows)
python halo\proyectos\parpadeo555\disenar.py   # la IA diseña; el Halo lo muestra paso a paso
python halo\pruebas\test_nativo.py          # el motor contra KiCad
python halo\pruebas\test_vista_pcb.py       # la vista de placas sobre las demos de KiCad
```

Placa por etapas, con el Python de KiCad:

```
"%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe" -m halo.kicad.pcb halo\proyectos\parpadeo555 crear
... acomodar | rutear [--motor freerouting] [--redes A,B] | zona GND B.Cu | verificar
```

| Carpeta | Qué hace |
|---|---|
| `servidor.py` | sirve la interfaz y empuja cambios por Server-Sent Events; sin sondeo en reposo |
| `vigia.py` | avisos de cambio de archivos del sistema (ReadDirectoryChangesW), sin dependencias |
| `proyectos.py` | proyectos como pestañas: estado inicial de una carpeta, nombre, aviso de apertura de los motores |
| `lanzador/` | `Halo.exe`: Halo.cs, el icono dibujado en Python y el script que los compila; `capturar.py` fotografía una página del Halo con Edge sin ventana por DevTools (evidencias) |
| `chat/servicio.py` | chats de Claude Code por proyecto: un proceso del binario oficial por turno (flujo JSON), permisos, detener, ramas, consola y Remote Control |
| `chat/transcripcion.py` | historial leído de la transcripción oficial de Claude Code (`~/.claude/projects`) |
| `documentos/servicio.py` | documentación viva en `<carpeta>/docs`: memoria Markdown e informe LaTeX con secciones generadas que avisan cuando su fuente cambió; figuras con kicad-cli, PDF con MiKTeX, pandoc; guía de etapas |
| `documentos/datos.py`, `plantillas.py`, `markdown.py`, `cli.py` | lo que el proyecto sabe de sí mismo, las plantillas, el conversor Markdown→HTML sin dependencias y la orden `python -m halo.documentos.cli` |
| `editor.py` | tus cambios a mano en esquemático y placa, con copia previa y deshacer |
| `ui/` | ventana: halo, lienzos 2D con zoom y arrastre, capas, panel de verificación, bitácora, chat, documentos; una sola base de botones en `halo.css` |
| `jev.py` | segunda opinión de Jev (TypeSafe): decisión tipada con probabilidad y confianza; clave sólo en `TYPESAFE_API_KEY` |
| `kicad/sexpr.py` | lee y escribe el formato S-expresión de KiCad sin pérdida |
| `kicad/biblioteca.py` | símbolos oficiales instalados, aplanando los derivados |
| `kicad/geometria.py` | la misma matriz de rotación y espejo que usa eeschema |
| `kicad/esquematico.py` | constructor: colocar, cablear, potencia, etiquetas, bloques, mover módulos, deshacer, guardar |
| `kicad/ruteo.py` | enrutado ortogonal A* que no toca cuerpos, pines ni cables ajenos |
| `kicad/reglas.py` | revisión de dibujo: solapes, cables sobre cuerpos, extremos sueltos, cruces |
| `kicad/oraculo.py` | KiCad juzga: ERC, netlist comparada con la intención, SVG |
| `kicad/sesion.py` | estado del halo y bitácora con recibos, visibles al instante |
| `kicad/vista.py` | convierte cualquier `.kicad_sch` en dibujo para el lienzo |
| `kicad/placa.py` | motor de placa con `pcbnew`: crear, colocar en dos caras, rutear, reglas, serigrafía, verificar |
| `kicad/acomodo.py` | acomodo con oficio: módulos, calor, altura, señales rápidas, dos caras, rótulos |
| `kicad/perfil.py` | perfil de cada huella: altura del modelo 3D, calor, sensibilidad, señales rápidas |
| `kicad/cobre.py` | cobre por zonas: prioridades, planos divididos, áreas prohibidas, costura, bajadas a plano |
| `kicad/clases.py` | clases de red del `.kicad_pro`: ancho, holgura y vía por red |
| `kicad/pcb.py` | las etapas de la placa desde la línea de comandos |
| `kicad/freerouting.py` | Freerouting supervisado: DSN con las reglas por red, sin interfaz |
| `kicad/vista_pcb.py` | convierte un `.kicad_pcb` (2 a 8 capas) en dibujo: zonas, capas con nombre, dos caras, pendientes |

Pruebas con el Python de KiCad: `halo\pruebas\test_placa.py` (cobre, dos caras, perfil, serigrafía).

Plan de trabajo por etapas: `docs/HALO-PLAN.md`.

### Icono

La **aureola**: un anillo dorado que flota sobre los tres puntos crema del Halo, en un cuadro café de
esquinas redondeadas. `lanzador/icono.py` lo dibuja con campos de distancia con signo (cuadro redondeado,
elipse, círculos) y supermuestreo por pixel (4×4 hasta 64 px, 3×3 en 128 y 256), sin bibliotecas gráficas.

| Salida | Contenido | Quién la usa |
|---|---|---|
| `build/halo/halo.ico` | 16, 24, 32, 48, 64, 128 y 256 px, una imagen PNG por tamaño | `construir.ps1` la incrusta en `Halo.exe` (`/win32icon`) |
| `halo/ui/halo.png` | 64 px | favicon de la página: Edge lo usa como icono de la ventana y de la barra de tareas |

- Cada tamaño se dibuja aparte; en 24 px o menos la aureola y los puntos engordan y se quitan los brillos para que se lean.
- `construir.ps1` vuelve a dibujar el icono antes de compilar `Halo.exe`.
- `ui/index.html` pide el favicon como `/halo.png?v=aureola`. Edge guarda los favicons por dirección en el perfil de la ventana
  (`build/halo/ventana`), así que **al cambiar el icono hay que cambiar ese `?v=`**; el servidor ignora la consulta.
- Un acceso directo de Windows debe tomar el icono de `Halo.exe,0`: si apunta a `build/halo/halo.ico`, el explorador sigue
  mostrando el de su caché aunque el archivo cambie. `ie4uinit.exe -show` refresca esa caché.

### Simulación eléctrica

La pestaña **Simulación** integra ngspice: punto de operación, transitorio y AC,
curvas, mediciones, historial y cancelación. Incluye ejemplos RC y 555 ideal.
Para agentes: `python -m halo.simulacion --ejemplo rc` o `/api/simulacion`.
Configuración de modelos, API, pruebas y límites: [HALO-SIMULACION.md](../docs/HALO-SIMULACION.md).

### Fabricación

El panel PCB/3D incluye **Preparar para JLCPCB**: DRC, Gerbers, taladros e informe
ligado a la revisión. Exportar no aprueba el diseño ni realiza un pedido.
API para agentes: `/api/fabricacion`.
