# Chat nativo: diagnóstico y límites

Este documento conserva el diagnóstico histórico de **SmolLM2-135M**. El modelo
principal y el prompt cambiaron a Qwen3.5; ver [QWEN.md](QWEN.md) para la versión
actual. Los ejemplos de conversación descritos abajo se retiraron del runtime.
Las mediciones de esta página no son mediciones de Qwen.

La versión inicial contestaba `hola` con una historia en inglés sobre un libro.
Se reprodujo con el mismo GGUF fuera de QEMU. Faltaba el mensaje de sistema que
define al asistente y el idioma. Los delimitadores no estaban intercambiados:
en este archivo `<|im_start|>` es 1 y `<|im_end|>` es 2.

La comparación usa el mismo SmolLM2-135M-Instruct Q8_0 con el runtime oficial
[llama.cpp b10887](https://github.com/ggml-org/llama.cpp/releases/tag/b10887),
temperatura cero y 32 tokens. Los 36 tokens del prompt inicial con mensaje de
sistema coinciden exactamente. También se observan respuestas poco pertinentes
en español con el runtime de referencia: añadir solamente una instrucción de
idioma no convierte este modelo pequeño en un asistente general fiable.

El contexto actual incluye ejemplos de conversación en español. Son mensajes de
entrada del modelo; el programa no compara el texto con `hola` para devolver una
respuesta prefabricada. Cada respuesta ejecuta el transformer. Los ejemplos ayudan
al saludo, la presentación y la cortesía; no garantizan razonamiento, aritmética,
conocimiento correcto ni obediencia a cualquier petición.

El chat reserva espacio para terminar cada turno, reconoce los tokens de fin y
limita la salida a 32 tokens desde la consola. Si llena el contexto, lo reinicia;
no hay memoria persistente. El estado final diferencia fin natural, límite,
cancelación y entrada rechazada. La salida del chat no autoriza acciones del sistema.

Escape se comprueba entre capas, incluso durante la lectura inicial del prompt.
La capability Input permite consultar o reconocer su evento sin consumir otras
teclas. La cancelación invalida el contexto parcial; el siguiente turno comienza
de nuevo. Durante `/ai`, una cancelación evita enviar una operación al broker.

La consola conserva secuencias UTF-8 divididas entre tokens y dibuja los caracteres
Latin-1, incluidas tildes y ñ. Otros caracteres muestran `?`. El tokenizer corrigió
contracciones y grupos de espacios/saltos de línea según el pretokenizador SmolLM;
todavía no implementa todas las categorías Unicode de su expresión regular.

## Cálculo y precisión

El prefill actualiza todas las capas y la caché KV, pero omite la proyección del
vocabulario cuando no se necesita elegir el siguiente token. Se comprobó igualdad
bit a bit de los 49.152 logits del último paso frente a calcular la proyección
en todos los pasos.

Para pesos Q8_0, el producto matricial convierte cada bloque de activaciones a
enteros de 20 bits con escala propia. Acumula 32 productos en `i32` y aplica las
escalas al final del bloque, reduciendo el trabajo flotante que TCG debe emular.
Los pesos del GGUF permanecen iguales. La cota es
`32 * 128 * 524287 = 2147479552 < INT32_MAX`.

Es una aproximación adicional, validada frente a la ruta flotante anterior:
393.216 logits, error absoluto máximo 0,00042725, RMS 0,0000715 y elección del
token máximo idéntica en los ocho pasos de la prueba. Esos casos no garantizan
igualdad para toda conversación. Los formatos F32/F16 y dimensiones fuera del
límite de la ruta optimizada conservan el cálculo flotante.

Las pruebas finales en host con esta ruta pasan saludo, presentación, cortesía,
fin natural, límites, cancelación y recuperación; el planner también conserva
sus casos de acción y su comprobación de caché. La comprensión en español
sigue teniendo los límites descritos arriba.

## Pruebas

```bash
source scripts/env.sh
bash scripts/test_chat_host.sh
bash scripts/test_planner_host.sh --self-test
python scripts/test_tokenizer_host.py
python scripts/test_matmul_host.py --model models/smollm2-135m-instruct-q8_0.gguf
python scripts/test_m4_kernel_host.py
make all
python scripts/qemu_chat_test.py --regression --timeout 1500
```

Las pruebas del guest guardan respuestas, razones de terminación, latencias,
capturas y hashes de los binarios en `build/m3-test/<ejecución>/`. Una prueba
interrumpida se marca incompleta, aunque algunos casos hayan pasado.

Mediciones de QEMU TCG del 14 de septiembre de 2026:

| Caso | Tiempo de pared | Resultado |
|---|---:|---|
| Saludo con contexto breve y ruta flotante anterior | 231,460 s | `Hola!`, 3 tokens |
| Saludo con ejemplos en español y Q20 | 165,544 s | `Hola, ¿cómo te llamas?`, 13 tokens |
| Presentación, segundo turno Q20 | 48,517 s | `Soy Erlan, el asistente de ErlanOS.`, 14 tokens |
| Cortesía, tercer turno Q20 | 27,105 s | `De nada.`, 4 tokens |

El saludo final es un 28,5 % más rápido de extremo a extremo. Las cargas son
distintas: el prompt final tiene 112 tokens frente a 36 del anterior; esta tabla
no es una comparación de velocidad de un único producto matricial. La inferencia
todavía tarda mucho con TCG. La primera medición pertenece a una ejecución
interrumpida después del saludo; no representa una regresión completa aprobada.

La regresión final en `build/m3-test/20260914-124908-rhfjqd4x/result.json` pasó
sus criterios en 335,704 s, con cancelación en 0,089 s y la entrada en espera
conservada. **No demuestra conversación general correcta:** la petición de
enumerar números recibió otra presentación del asistente. Esa comprobación sólo
verificaba que la salida no excediera 32 tokens; terminó naturalmente con 14 y
no activó el límite. La rama que corta por presupuesto sí se ejercitó en host
con un límite de un token. El modelo sigue produciendo respuestas fuera de tema.

En el diagnóstico inicial, la virtualización estaba desactivada y la prueba WHPX
no pudo arrancar. Tras activarla el usuario, Windows informa `HypervisorPresent=true`
y QEMU puede inicializar WHPX. Los campos VMX de WMI aparecen ocultos cuando el
hipervisor ya está activo; la prueba real de QEMU determina su disponibilidad.
No se cambiaron opciones de BIOS ni características de Windows desde el agente.

## Arranque acelerado en Windows

`make run` consulta `scripts/qemu_accel.py`: usa WHPX en Windows, KVM en Linux/WSL
cuando está disponible, o TCG con un diagnóstico de fallback. La sonda crea una
VM pausada, sin discos ni ventana, verifica QMP y cierra sólo su propio proceso.
La sonda comprueba disponibilidad; las regresiones del guest comprueban el arranque
completo y la ejecución del modelo.

La configuración WHPX usa `-cpu max,vmx=off`: este OVMF intentaba activar VMX
anidado y provocaba una excepción en el firmware. ErlanOS no necesita virtualizar
otra CPU dentro del guest. Esta opción sólo afecta la CPU virtual de QEMU.

La aceleración también reveló una inicialización incorrecta del estado SIMD:
los nuevos hilos recibían `MXCSR=0` y la primera división inexacta de `mx_pow`
disparaba `#XM`. El scheduler ahora inicializa el área XSAVE con `MXCSR=0x1f80`
y control x87 `0x037f`, manteniendo el redondeo estándar y las excepciones
enmascaradas. `python scripts/test_xstate_host.py` reproduce el estado anterior
y verifica el nuevo estado con XSAVE/XRSTOR e instrucciones SSE nativas.

Prueba explícita: `python scripts/qemu_chat_test.py --regression --accel whpx`.
Los tiempos del host y las mediciones anteriores bajo TCG no deben presentarse
como tiempos de WHPX.

La regresión acelerada pasó en 68,123 s el 14 de septiembre de 2026, con los
mismos prompts, modelo Q8_0 y cálculo Q20. Evidencia:
`build/m3-test/20260914-132350-r_alx83q/result.json`.

| Turno | WHPX, tiempo de pared | TCG anterior |
|---|---:|---:|
| `hola` | 11,434 s | 165,544 s |
| `Como te llamas?` | 3,807 s | 48,517 s |
| `gracias` | 2,134 s | 27,105 s |

El primer turno mejoró unas 14,48 veces. El arranque previo a la entrada tardó
aproximadamente 39 s en esa ejecución; no está incluido en la latencia del saludo.
Escape respondió en 0,094 s y conservó `/estado` en espera. Las tres respuestas
coincidieron con la prueba anterior. La petición de contar números volvió a recibir
una respuesta fuera de tema: la aceleración mejora el tiempo, pero la calidad del
modelo sigue siendo limitada.
