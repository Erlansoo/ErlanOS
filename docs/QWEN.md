# Qwen dentro de ErlanOS

El motor de inferencia sigue ejecutándose en el servicio `model`, Ring 3, escrito
en Erlan. Qwen3.5 requiere atención GQA con compuerta y estado recurrente
GatedDeltaNet; `services/model/qwen35.e` implementa su ruta de texto. El runtime
Qwen2 añade los sesgos Q/K/V y RoPE NEOX necesarios para Qwen Coder.

El cambio responde al fallo visible de SmolLM2: repetía preguntas del usuario
incluso en el motor de referencia. No contiene respuestas de ejemplo.

## Identidad y capacidades (mensaje de sistema)

El mensaje de sistema vive en `services/model/chat.e` (`fn chat_system`) y es la única
fuente de lo que el modelo cree que es. Se emite en trozos de menos de 255 bytes (límite
del buffer de `chat_text`), sin acentos por el pretokenizador. Dice, en este orden: que es
la mente de ErlanOS, un SO propio escrito en Erlan; que NO es el kernel sino un programa
aislado con permisos limitados; que puede conversar, escribir texto o código y explicar
el sistema; que NO puede actuar (ejecutar, gestionar recursos, cambiar permisos, navegar);
los comandos que el usuario puede escribir; y que debe decir sin inventar lo que no puede.

Lecciones medidas en host (Qwen3.5-2B): sin esa distinción entre hablar y actuar, el modelo
se atribuye capacidades falsas ("gestiono recursos, ejecuto comandos") o, si se le dice que
SOLO puede conversar, se niega a escribir código. La redacción actual pasa 10/10 en
`scripts/test_chat_host.sh --semantic`. `scripts/test_coder_host.py` lee el prompt de
`chat.e` para mantener paridad con la referencia.

**Pre-llenado al arranque.** `model_warmup()` procesa el mensaje de sistema justo después
de cargar el modelo (y en reposo tras un reinicio de contexto), así el primer turno paga
solo los tokens del usuario. En el host cuesta ~0,4 s por token; bajo TCG serían minutos.
El contexto sigue limitado a 512 tokens: el sistema ocupa unos 190, y al llenarse se
reinicia la conversación y se vuelve a inyectar la identidad.

## Modelos locales

| Perfil | Modelo | Archivo | Tamaño verificado |
|---|---|---|---:|
| `qwen35` | Qwen3.5-2B Q8_0 | `models/qwen3.5-2b-q8_0.gguf` | 2.012.012.800 bytes |
| `coder` | Qwen2.5-Coder-1.5B-Instruct Q8_0 | `models/qwen2.5-coder-1.5b-instruct-q8_0.gguf` | 1.894.532.160 bytes |

Se carga **un modelo por arranque**. El perfil de código no mantiene un segundo
modelo residente. QEMU recibe 4 GiB de RAM; en la primera prueba de Qwen2B el
proceso de Windows ocupaba unos 2,4 GB al terminar el arranque.

Fuentes y huellas:

- [Modelo original Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B),
  [GGUF Q8_0 de Unsloth, revisión fijada](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/blob/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q8_0.gguf).
  SHA256 `1b04acba824817554f4ce23639bc8495ff70453b8fcb047900c731521021f2c1`.
- [GGUF oficial Qwen Coder 1.5B](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/blob/main/qwen2.5-coder-1.5b-instruct-q8_0.gguf).
  SHA256 `507de59046601282ba768a9789900e6ccf60ed93ddf346730b7c68eb0715bc47`.

También se descargó y comparó Qwen3.5-0.8B Q8_0 (811.843.840 bytes). En la prueba
de contexto incremental pasó 9/10 casos en llama.cpp y falló recordar el nombre
Lucia, mientras 2B pasó 10/10. El fallo se reprodujo también con la plantilla
oficial, sin etiquetas de razonamiento en el historial. Se conserva 2B por defecto; no se presenta el archivo
0.8B como perfil nativo validado. Comparación: `build/qwen35-small/reference-chat.json`.

Ambos perfiles principales se distribuyen con Apache-2.0. Los pesos y la procedencia local quedan en
`models/` o `build/`, fuera de git. No se descargó un proyector visual: esta versión
procesa texto. Qwen3-Coder-30B-A3B tiene 30B parámetros totales; los 3B activos no
lo convierten en un archivo de 3B apropiado para este perfil de memoria.

## Arranque y uso

```bash
source scripts/env.sh
make run                  # Qwen3.5-2B
make run MODEL=coder      # después de cerrar la VM anterior
make run MODEL=smollm     # modelo histórico, si está descargado
```

El generador `scripts/build_boot_image.py` crea un archivo de disco GPT/FAT32 y
verifica las dos tablas GPT, cadenas FAT, directorios y hashes del contenido.
No formatea ni monta un disco físico. VVFAT tenía una capacidad de unos 504 MB;
no servía para estos GGUF. Cada archivo del nuevo perfil debe ser menor de 4 GiB.

La consola muestra el nombre leído del GGUF. `Enter` envía la petición;
`Shift+Enter` inserta una nueva línea sin enviarla, para conservar el formato de
código. La entrada admite hasta 240 bytes, incluidos saltos de línea. Retroceso
permite borrar también esos saltos. `Esc` cancela y reinicia el estado
parcial; `/estado`, `/modo foco`, `/modo normal` y `/panel` conservan la API del
sistema. El clasificador experimental `/ai` se abstiene con Qwen: su antiguo
prompt, caché y márgenes estaban calibrados para SmolLM2. Validarlo para Qwen
es trabajo separado; el texto generado por chat no concede permisos ni ejecuta
el código que muestra.

## Verificación

```bash
python scripts/test_qwen_tokenizer.py
python scripts/test_qwen35_reference.py
python scripts/test_matmul_host.py --model models/qwen3.5-2b-q8_0.gguf
bash scripts/test_chat_host.sh --model models/qwen3.5-2b-q8_0.gguf --semantic
bash scripts/test_chat_host.sh --model models/qwen3.5-2b-q8_0.gguf --controls
python scripts/qemu_chat_test.py --semantic --model models/qwen3.5-2b-q8_0.gguf --accel whpx --timeout 1500
python scripts/test_coder_host.py --exe build/chat-host/chat_test.exe --native-only --expect-avx2
python scripts/test_boot_image.py
python scripts/test_flat_image.py --elf build/user/model.elf
python scripts/test_m4_kernel_host.py
python scripts/test_input_host.py
```

Qwen2B pasó los diez casos semánticos en host nativo: el transcript del usuario,
recordar el nombre Lucia, responder `4`, contar `1 2 3 4 5` y escribir una función
Python de suma. El código se inspecciona por AST; no se ejecuta el texto generado.
La evaluación de contenido es independiente de las pruebas de cancelación y
límites. Resultado: `build/qwen35/native-semantic.json`.

Los diez casos también pasaron dentro de QEMU con WHPX y 4 GiB. Escape canceló
en 0,092 segundos y el `/estado` escrito mientras se cancelaba llegó al broker.
Evidencia de esta prueba anterior a AVX2:
`build/m3-test/20260914-135531-g5w3obqy/result.json`.

El tokenizer coincide en 30/30 casos con llama.cpp b10887, incluyendo español,
marcas combinadas, otros alfabetos, espacios, código y tokens añadidos. Usa tablas
de categorías Unicode 16.0 generadas por Python; el runtime no depende de Python.
El parser valida rangos de metadatos y tensores antes de leer pesos, y las tablas
hash crecen según el vocabulario para evitar llenar la tabla anterior de 131.072.

La comparación independiente de predicciones contra llama.cpp coincide en los
ocho tokens evaluados, incluidos contexto largo y aritmética. Q20 y el producto
escalar de referencia coinciden en el token elegido para los ocho pasos
numéricos; sus 1.986.560 logits difieren como máximo aproximadamente 0,00013.
Esto no promete igualdad para cualquier conversación o entre todos los kernels.

La multiplicación Q20 tiene una especialización `matmul_fast.e`, también en
Erlan, compilada por separado para AVX2. Antes de llamarla se comprueban CPUID,
XSAVE, OSXSAVE y XCR0; sin soporte se conserva la ruta portátil. Las pruebas
eliminan individualmente cada requisito del detector. El resto del servicio
se compila con instrucciones básicas x86-64 y `-O3`, sin exigir AVX2.

En el modelo 2B completo, AVX2 y la ruta portátil produjeron los mismos
1.986.560 logits bit a bit. Coder produjo 1.215.488 logits también idénticos
entre ambas rutas. El scratch Q20 admite las 8.960 columnas FFN del Coder,
que anteriormente caían en el cálculo escalar. La carga de servicios incluye
todo BSS dentro de la imagen plana, incluso cuando supera los antiguos 64 KiB.

Coder con AVX2 pasó creación y reparación en host con las mismas respuestas que
la referencia. Tiempos de dos peticiones: 30,0 y 19,9 segundos; la comparación
anterior sin estos cambios era 71,9 y 51,3 segundos. Son mediciones de esta máquina,
no una promesa de velocidad para cualquier petición. Resultado:
`build/qwen-coder/code-result-avx2.json`.

En QEMU, la versión AVX2 de Qwen2B respondió `hola` en 20,139 s y la pregunta
de identidad en 18,653 s, frente a 59,029 y 46,601 s antes. Las respuestas fueron
iguales. `/ai` no disponible no lanzó inferencia ni cambió modo/revisión; Escape
y el `/estado` en cola pasaron. Evidencia:
`build/m3-test/20260914-141353-jiruc0dc/result.json`.

Se conserva un fallo de Coder: al aplanar la petición de reparación y su fragmento
Python en una sola línea, dejó `return a - b` sin corregir. La suite lo marcó
como fallo, aunque la misma petición con sus saltos de línea había pasado en host.
Resultado: `build/m3-test/20260914-141100-8n6s6iz_/result.json`. Es una limitación
de sensibilidad al formato del modelo; no se interpreta un EOS como prueba de
que el código esté bien.

La repetición final dentro de QEMU conservó exactamente las dos peticiones del
host: `Shift+Enter` introdujo los saltos de línea y se mantuvieron los cuatro
espacios de sangría. Coder pasó creación y reparación: en ambos casos produjo
`return a + b`, verificado por AST sin ejecutar código. Tardó 30,573 y 25,221 s;
Escape canceló en 0,091 s y se conservó `/estado` en cola. Resultado y captura:
`build/m3-test/20260914-142312-jutaa15y/result.json` y `turn-02.png`.
Esto valida esos ejemplos con el formato correcto; el fallo anterior permanece
registrado y el modelo sigue pudiendo equivocarse.

La entrada multilínea tiene pruebas del bucle real del servicio: envío,
borrado de saltos, límite de 240 bytes y cancelación. Las pruebas del teclado
cubren ambos Shift y las teclas extendidas. El repintado de consola se comparó
píxel por píxel tras wrap, borrado y scroll, conservando panel y halo.

## Límites

La inferencia nativa usa CPU. WHPX acelera la CPU virtual, pero no convierte la
GPU de Windows en una GPU disponible para ErlanOS. La primera prueba sin la
especialización AVX2 tardó 59 segundos en contestar `hola` con Qwen2B; parte del
tiempo corresponde a leer el prompt y parte a generar la respuesta. Cargar los
2 GB desde el disco también cuesta tiempo al arrancar.

La ventana nativa está limitada a 512 tokens y la salida a 64 tokens. Cuando la
ventana se llena se reinicia y se informa en consola; no hay memoria persistente.
Se mantiene el contexto incremental, incluidas las etiquetas vacías de razonamiento
de turnos previos, para evitar recalcular todo el historial. Se verificó ese mismo
contexto frente a la referencia. La consola dibuja Latin-1; emojis y otros
caracteres fuera de la fuente se ven como `?`, aunque el serial conserva UTF-8.

Las pruebas pequeñas comprueban la regresión observada, no una capacidad general
de programación o razonamiento. Generar código todavía no implementa el ciclo
automático de compilar, aislar, medir y promover previsto para M5/M6.
