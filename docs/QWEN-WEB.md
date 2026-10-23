# Qwen local con consulta web

Implementación del 21 de septiembre de 2026 para el chat de Windows en
http://127.0.0.1:8088. El perfil predeterminado ejecuta Qwen3.5-4B Q4_K_M,
con contexto 16.384. Qwen3.5-2B Q8_0 sigue disponible como perfil alternativo.

## Funcionamiento

El chat entrega dos herramientas al modelo. Qwen decide si necesita llamarlas;
la interfaz ejecuta la consulta, devuelve la evidencia al modelo y continúa
la respuesta. El límite inicial es seis ciclos, configurable en Settings →
Agentic. No es una búsqueda disparada únicamente por palabras clave.

- **web_search:** busca mediante el servidor oficial de Exa, en acceso anónimo
  sin API key ni cuenta de pago. Se envían la consulta, el número de resultados
  y una instrucción fija de priorizar fuentes primarias. No se envía el historial
  del chat como parte del protocolo. El proveedor aplica límites gratuitos.
- **web_read:** descarga texto de una URL HTTPS pública, con verificación TLS.
  Conserva el enlace y limita el tamaño. No usa sesiones del navegador.

Las herramientas aparecen en Add to chat → Tools → Server (2/2). La conversación
se genera en el equipo; las búsquedas y las lecturas sí necesitan Internet.
Para comprobar hardware, bibliotecas, APIs o datos actuales se instruye al
modelo a buscar y citar las fuentes. Para saludos y cálculos sencillos puede
responder directamente. Esto es una instrucción, no una garantía: un modelo
pequeño también puede elegir mal una herramienta o interpretar mal una fuente.

Los chats existentes se conservan. Un chat nuevo toma el mensaje de sistema
actualizado; continuar un historial con información falsa puede arrastrar errores.

## Arranque y archivos

Desde la raíz del repositorio:

```powershell
./build/qwen-local-windows/iniciar.ps1 -Modelo 2b -Contexto 32768

# Para iniciar sin las herramientas de Internet, tras detener el servidor:
./build/qwen-local-windows/detener.ps1
./build/qwen-local-windows/iniciar.ps1 -Modelo 2b -SinInternet
```

- scripts/qwen_web.py: servidor MCP stdio, solo biblioteca estándar de Python.
- build/qwen-local-windows/iniciar.ps1: inicia el proceso como hijo de llama.cpp;
  genera mcp-web.json con las rutas de este equipo. No abre otro puerto.
- build/qwen-local-windows/ui-config.json: instrucciones, muestreo y límite de ciclos.
- tests/test_qwen_web.py: protocolo y límites de acceso a la red.

No se habilitan herramientas de terminal ni lectura/escritura de archivos. El
lector bloquea direcciones privadas, loopback y redirecciones hacia esas redes;
conecta a las IP previamente verificadas y mantiene validación de certificado
y nombre. Descarga como máximo 2 MB y devuelve hasta 14.000 caracteres de una
página, por defecto 8.000. La búsqueda devuelve hasta 12.000 caracteres.

## Límites conocidos

- El acceso anónimo de Exa puede agotarse o cambiar. Un error se muestra como
  fallo de consulta; no se sustituye por evidencia inventada.
- El lector no ejecuta JavaScript, no inicia sesión y no extrae PDF. La búsqueda
  puede devolver fragmentos indexados de PDF, que no equivalen a haber leído
  el documento completo.
- Internet no aumenta los parámetros, no entrena el modelo ni valida código.
  Las referencias deben respaldar las afirmaciones; citar por sí solo no basta.
- El contexto de 32.768 sigue siendo compartido entre historial, herramientas
  y salida. Las páginas se acotan para evitar llenarlo con una sola consulta.

## Verificación

```powershell
py -3 -m unittest discover -s tests -p test_qwen_web.py -v
```

Pasaron siete pruebas: rechazo de redes privadas y DNS mixto, esquemas y
credenciales no admitidos, redirecciones a localhost, limpieza y truncamiento
de HTML, conservación de evidencia de búsqueda, manejo de errores del proveedor
y negociación/listado de herramientas MCP. La consulta real a Exa recuperó
documentación oficial de Arduino UNO R3; el lector recuperó su página oficial.

Prueba completa en la interfaz: el 2B eligió web_search, consultó una fuente
oficial y acertó el ATmega328P. Sin embargo, añadió incorrectamente que hacía
falta un interruptor externo para el LED integrado. En otra prueba pidió buscar
17 + 25, pese a la instrucción de no buscar para cálculos sencillos; esa búsqueda
innecesaria no se ejecutó. La conexión funciona, pero la selección y el uso de
evidencia del modelo 2B siguen siendo poco fiables. No se declara resuelto el
problema de alucinaciones. Resultados: build/qwen-local-windows/verification-web.json.

En el navegador actual se habilitó Always allow únicamente para web_search y
web_read. Otros navegadores pueden pedir ese permiso la primera vez. Se deja
un chat nuevo listo, conservando tanto el historial anterior como la prueba.

## Modelo siguiente

La GTX 1050 Ti tiene 4 GB y Windows también consume memoria gráfica. Se instaló
Qwen3.5-4B Q4_K_M (2.740.937.888 bytes), con su SHA-256 publicado verificado:
00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4.
En este equipo se cargaron 22 de 33 capas en GPU; las demás quedan en RAM/CPU.
Con 16.384 tokens, el servidor usó aproximadamente 3.380 MiB de VRAM al cargar.
La prueba Blink generó 200 tokens a 9,36 tokens/s y fue correcta; es una prueba
acotada, no una garantía general. Un Qwen3-8B cuantizado necesita más RAM/CPU
y no cabe entero junto al contexto y Windows en esos 4 GB. Cambiar de modelo no
sustituye las herramientas web.

Referencias:

- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- https://github.com/exa-labs/exa-mcp-server
- https://exa.ai/mcp
- https://huggingface.co/Qwen/Qwen3.5-4B
- https://huggingface.co/unsloth/Qwen3.5-4B-GGUF
