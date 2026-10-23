# Simulación nativa de Halo — coordinación con Claude

Fecha: 2026-09-16. Primera integración implementada y probada con ngspice 46 / KiCad 10.0.6 en Windows.

## Alcance de esta entrega

- Adaptador ngspice por DLL en un proceso independiente, con cancelación y tiempo límite.
- Preparación desde una netlist exportada por KiCad y asignaciones explícitas en un archivo JSON auxiliar. No modificar esquemáticos ni placas.
- Punto de operación, transitorio y análisis AC; curvas, mediciones y criterios numéricos para agentes y personas.
- Instantáneas reproducibles, hash de circuito/configuración/modelos y resultados obsoletos al cambiar las entradas.
- Pestaña Simulación en el mismo Halo. Pruebas RC analíticas y ejemplo 555 con modelo funcional declarado.
- API local y CLI Python compartiendo el motor. Los resultados nunca sustituyen ERC/DRC ni una validación del hardware real.

## Archivos entregados

Nuevo paquete `halo/simulacion/`, pruebas `halo/pruebas/test_simulacion.py`, vista `halo/ui/simulacion.js` y estilos independientes. Puntos compartidos: `halo/servidor.py`, `halo/contexto.py`, `halo/ui/index.html`, `halo/ui/halo.js`.

## Fuera de esta entrega

Chat autónomo, cambios automáticos al circuito, modelos de fabricante implícitos, firmware, térmica/EM, tolerancias Monte Carlo y co-simulación. Un modelo 555 funcional sirve para comprobar temporización, no para certificar consumos ni límites físicos del componente.

## Uso en Halo

Iniciar normalmente `python -m halo.servidor` y abrir
`http://127.0.0.1:8765/?pestana=simulacion`.

En **Simulación**, elegir un ejemplo RC (transitorio, AC o punto de operación) o
555, y pulsar **Simular**. Se muestran mediciones, criterios, gráficas con cursor,
historial, comparación y cancelación. Los ejemplos se identifican como
independientes: nunca verifican el proyecto abierto por accidente.

Para el proyecto abierto:

1. Desplegar **Configurar modelos, fuentes y mediciones**.
2. **Leer conexiones y crear plantilla** exporta una netlist XML usando KiCad.
3. Completar la configuración; los componentes `ASIGNAR` bloquean la simulación.
4. Guardar y simular. Guardar no significa que la configuración sea válida: se
   verifica al preparar. Sólo se escribe `.halo/simulacion/config.json`.
5. Las curvas del proyecto vigente incluyen botones de referencias que vuelven
   al esquemático y seleccionan el componente correspondiente.

La configuración auxiliar del proyecto `halo/proyectos/parpadeo555` quedó lista
sin cambiar sus archivos KiCad. El banco también está en
`halo/simulacion/bancos/555-proyecto.json`. Los proyectos grandes necesitan sus propios modelos
y banco: no se asignan modelos inventados.

## API para agentes

Consultar `/api/capacidades` y `/api/simulacion` devuelve las capacidades,
disponibilidad del motor, configuración guardada e historial del proyecto activo
y ejemplos. `GET /api/simulacion?id=<id>` entrega estado y resultados.

Todas las operaciones usan `POST /api/simulacion` con JSON:

| op | Campos | Resultado |
|---|---|---|
| inventario | proyecto_id, expected_revision | Componentes, conexiones, redes y plantilla |
| configurar | proyecto_id, expected_revision, config | Guarda sólo el archivo JSON auxiliar |
| preparar | proyecto_id, expected_revision; config opcional | Exporta y congela las entradas; devuelve id |
| preparar | ejemplo: rc / rc_ac / rc_op / 555 | Prepara un ejemplo independiente |
| ejecutar | id; timeout opcional, 1–120 s | Arranca y devuelve inmediatamente |
| cancelar | id | Detiene el proceso de esa ejecución |
| comparar | id, base | Diferencias de mediciones equivalentes, con vigencia |

`proyecto_id` y `expected_revision` se obtienen de `/api/estado` o `/api/contexto`.
Cada `id` se ejecuta una sola vez; para repetir hay que preparar otro. Un error,
timeout, cancelación o falta de datos nunca devuelve `cumple`.

El criterio global se refiere **únicamente a los límites numéricos declarados**;
`completado` significa que ngspice terminó, no que el diseño esté aprobado.
Una medición sin suficientes cruces para frecuencia queda `no_verificado`.
Una ejecución sin límites queda `sin_criterios`. Los resultados AC se muestran
como magnitud; los vectores complejos originales se conservan en `crudo.json`.

## Contrato de configuración v1

Ver un ejemplo completo en `halo/simulacion/bancos/555-proyecto.json`.

- `version: 1`, `nombre`, `tierra`: nombre exacto de una red exportada.
- `componentes`: mapa por referencia. Cada asignación declara `tipo`, `pines`
  en el orden requerido por el modelo y `valor_esquematico` exactamente como lo
  exportó KiCad. Si ese valor cambia, el banco requiere revisión explícita.
- R/C/L: `valor` numérico en ohmios/faradios/henrios. C/L admiten `inicial`.
  No se aceptan cadenas ambiguas como `1M`.
- V/I: `dc`, `ac` opcional, `pulso` opcional con siete números SI
  `[bajo, alto, retardo, subida, bajada, ancho, periodo]`.
- D/X: `modelo` nombra un `.model` o `.subckt` aportado explícitamente.
- `tipo: omitir` exige `motivo`. Las exclusiones se muestran en los resultados.
- `fuentes`: voltajes del banco sobre redes existentes; `positivo`, `negativo`
  opcional (tierra por defecto), `dc` y `ac` opcional.
- `modelos`: rutas relativas dentro del proyecto; `modelos_inline`: textos de
  bibliotecas autocontenidas. No se admiten `.include`, `.lib`, `.control`,
  `.osdi` ni módulos ejecutables; hay que consolidar las dependencias antes.
- `analisis`: `{tipo: op}`, `{tipo: tran, paso, fin, uic?}` o
  `{tipo: ac, inicio, fin, puntos}`; `puntos` indica puntos por década.
- `sondas`: 1–8 objetos `{id, red}` o `{id, corriente: referencia_de_fuente_V}`.
  La orientación de corriente es la convención SPICE de esa fuente.
- `medidas`: `{id, sonda, metrica, min?, max?, desde?, hasta?}`.
  Métricas: min, max, final, pico_a_pico, valor_en (`en` obligatorio), frecuencia
  (`umbral` obligatorio; sólo transitorio). Los valores de medida usan V/A/Hz.
- `limitaciones`: explicaciones visibles del alcance del modelo y del ensayo.

Los valores numéricos asignados son parte del banco; no se inventa una conversión
de modelo a partir de la referencia comercial. Pueden representar una variante
del componente: quien configura el banco debe declarar esa intención.

## CLI nativa

```powershell
python -m halo.simulacion --ejemplo rc
python -m halo.simulacion --ejemplo rc_ac
python -m halo.simulacion --ejemplo rc_op
python -m halo.simulacion --ejemplo 555
python -m halo.simulacion --esquematico halo/proyectos/parpadeo555/parpadeo555.kicad_sch --config halo/simulacion/bancos/555-proyecto.json
python -m halo.simulacion --esquematico ruta/circuito.kicad_sch --plantilla
```

Usar Python de 64 bits; aquí se validó con el Python de KiCad:
`C:/Users/Erlan/AppData/Local/Programs/KiCad/10.0/bin/python.exe`.
El descubrimiento respeta `KICAD_DIR`; `HALO_NGSPICE_DLL` permite indicar otra DLL.
No se instaló software nuevo. La carga usa `ctypes` y el ABI público sharedspice.

## Reproducibilidad y concurrencia

Cada ejecución guarda en `build/halo/simulaciones/<id>/`:

- `config.json`, `componentes.json`, netlist XML exportada si proviene de KiCad.
- `circuito.cir` y `entrada.json`: circuito, modelos incorporados, sondas y comando.
- `meta.json`: revisión de diseño, hash de configuración/modelos y DLL, tiempos y estado.
- `crudo.json`: todos los vectores solicitados, incluida parte imaginaria AC.
- `resultado.json`: métricas y curvas reducidas conservando extremos para la UI.
- `worker.log`: salida auxiliar del proceso.

Las mediciones usan todos los puntos; no se calculan sobre las curvas reducidas.
La huella de la instantánea se comprueba antes y después de ejecutar. Los cambios
en diseño, configuración guardada o modelos externos marcan el resultado como
obsoleto. El resultado histórico permanece disponible y no se mezcla con la
verificación ERC/DRC. El deck autocontenido permite reproducir la entrada original.

Se usa el bloqueo existente de proyecto durante la exportación y preparación;
se libera antes de simular. Ngspice vive en un proceso separado y cancelarlo mata
ese proceso. Cada instancia del servicio limita a dos ejecuciones simultáneas.
No es un sandbox del sistema operativo ni impone un límite duro de memoria.
No ejecutar varios servidores Halo sobre la misma carpeta de resultados; la
recuperación al arrancar marca trabajos inconclusos como interrumpidos.

## Pruebas y resultados

- 16 pruebas en `halo/pruebas/test_simulacion.py`: ngspice real, RC contra fórmula
  analítica, AC, DC, 555 ideal, KiCad exportado, modelo faltante, instantánea
  alterada, modelos/config obsoletos, cancelación y timeout con procesos reales.
- 1 prueba HTTP en `halo/pruebas/test_simulacion_http.py`: preparar/ejecutar/
  consultar, capacidades, resultados, errores de contexto e identificadores.
- Regresión: 22 pruebas de fiabilidad y cuatro ejemplos del visor PCB pasan.
- `node --check` pasa para `halo.js` y `simulacion.js`.
- RC: aproximadamente 3.1606 V a una constante de tiempo y 4.96631 V a cinco.
- 555 exportado del proyecto: **1.05263 Hz**, frente a **1.05306 Hz** de la
  fórmula ideal `1 / (ln(2) · (R1 + 2 R2) · C1)`. El hash del diseño no cambió.
- Evidencia de la ejecución persistente: `build/halo-simulacion/555-resumen.json`.

La inspección visual automatizada no pudo realizarse: en esta sesión CUA no
ofreció ningún navegador conectado. La API y el motor sí se probaron; no se
presenta la revisión visual como completada.

## Límites concretos y siguiente entrega

Primera versión para esquemáticos de una hoja. La jerarquía se rechaza hasta
versionar correctamente sus hojas y dependencias. No hay autorreparación,
optimización de valores, fase/Bode en la UI, FFT, sweep dedicado, Monte Carlo,
firmware ni simulación térmica/EM. Tampoco se importan automáticamente modelos
del fabricante ni todas las propiedades `Sim.*` de KiCad.

El 555 de ejemplo es un modelo funcional propio con interruptores e histéresis,
comprobado contra la ecuación astable; **no es un macromodelo validado por TI**.
Sólo admite el montaje de ejemplo, TRIG=THRESH y RESET=VCC. El LED también es
genérico: no usar este banco para certificar corrientes, potencia o tolerancias.

Para Claude: el siguiente paso es conectar el agente al contrato anterior,
proponer variantes de configuración con una intención explícita, comparar
mediciones y presentar cambios sobre el diseño para revisión. Mantener separados
los cambios de configuración, los resultados de simulación y las ediciones de
esquemático/PCB.

Referencias: [sharedspice.h oficial](https://github.com/ngspice/ngspice/blob/master/src/include/ngspice/sharedspice.h),
[manuales de ngspice](https://ngspice.sourceforge.io/docs.html).

## Pulido de lógica y rendimiento — 2026-09-16

Entrega posterior centrada en corregir y optimizar esta integración, sin ampliar
los tipos de simulación ni editar diseños KiCad:

- `valor_en` y la localización de extremos temporales usan búsqueda binaria.
  Una consulta puntual cuesta O(log N), sin construir listas de toda la señal.
- Las ventanas de medición interpolan sus límites. Una ventana entre dos
  muestras ya puede medirse; antes quedaba vacía. No se extrapola fuera del eje.
  Los intervalos min/max/final/frecuencia se recortan al dominio disponible;
  `valor_en` fuera del dominio o de la ventana devuelve no verificado.
- La frecuencia acumula el número de cruces y sus extremos sin guardar una lista
  adicional. El intervalo de cruces es abierto por la izquierda y cerrado por
  la derecha. Sigue requiriendo al menos dos cruces ascendentes.
- Se comprueban eje creciente, dimensiones y valores finitos antes de publicar
  métricas. Se mantienen todos los puntos de curvas pequeñas y los extremos de
  las grandes. Sondas repetidas reutilizan el cálculo y reducción de su vector.
- Cada consulta del historial calcula una sola vez la revisión de cada proyecto
  y el hash de cada dependencia. La caché muere al terminar la consulta: no oculta
  cambios entre consultas. Los proyectos ajenos se filtran antes de calcular hashes.
- La interfaz no vuelve a descargar curvas terminadas ni reconstruye selectores
  si no cambiaron. Una revisión obsoleta sigue repintando el estado correspondiente.
  `GET /api/simulacion?id=...&resumen=1` entrega metadatos sin curvas.
- El cursor busca el punto más cercano en O(log N), con coordenadas calculadas una
  vez. Las respuestas de un proyecto anterior se descartan tras cambiar de proyecto.
- La preparación se publica mediante renombrado al terminar; un error limpia su
  directorio temporal. Si falla la publicación del arranque, el proceso lanzado
  se termina y se libera su plaza. La forma del JSON se valida antes de exportar.

Validación: 27 pruebas Python de simulación/API/lógica y una prueba JavaScript
de cursor, consultas repetidas, invalidación y cambio de proyecto. Las pruebas
reales RC/555 siguen pasando. La prueba JS es de lógica, no una revisión visual.

Microbenchmark local (`build/halo-optimizacion/benchmark.json`): 50 consultas
`valor_en` sobre 100.000 puntos, mediana de cinco repeticiones, pasaron de
1155,34 ms a 0,0554 ms, con valores iguales a tolerancia de 1e-12. Mide sólo
consultas puntuales en memoria; no representa una aceleración de ngspice ni del
tiempo total del flujo. Los datos numéricos crudos de las ejecuciones antiguas se
conservan; sus resúmenes históricos no se recalculan automáticamente con esta lógica.
