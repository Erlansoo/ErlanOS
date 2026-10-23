# Pasarela: la mente remota (Claude Code / Codex) al mando de ErlanOS

Estado: diseño aprobado, en construcción. Fecha: 2026-09-15.

## Idea

La "mente" de ErlanOS deja de ser un solo programa. Es una interfaz con dos
implementaciones que hablan la misma AI Kernel API (M4), con las mismas
capabilities y los mismos recibos:

| Mente | Dónde corre | Cuándo manda |
|---|---|---|
| Nube: Claude Code o Codex | fuera de ErlanOS, en la pasarela | cuando hay enlace con la pasarela |
| Local: Qwen (servicio `model`) | dentro de ErlanOS, Ring 3 | sin enlace, o como herramienta de la mente Nube |

Lo que corre dentro de ErlanOS es un cliente delgado: el chat. La inteligencia
remota vive en la pasarela. El kernel no distingue mentes: ambas son procesos
Ring 3 sin privilegios, y toda acción pasa por el broker `system` con recibo.

Precisión honesta: Claude Code no puede ejecutarse dentro de ErlanOS (Node.js,
sistema de archivos, TLS, POSIX). La pasarela es un puente declarado, no una
capa nativa. El camino nativo (TCP + TLS 1.3 + HTTP en Erlan) queda para después.

## Piezas

1. `scripts/gateway/erlan_gateway.py` (Windows, Python). Servidor UDP que
   ejecuta el agente sin interfaz y traduce entre el protocolo ELP1 y el agente.
   Adaptadores: `claude` (`claude -p --output-format stream-json
   --input-format stream-json --include-partial-messages`), `codex`
   (`codex exec --json`, reanudación con `resume --last`) y `echo` (agente
   determinista para pruebas sin credenciales).
2. Servicio `nube` en ErlanOS (Ring 3, Erlan). Abre la sesión ELP1, envía lo que
   el usuario escribe, recibe la respuesta por trozos y la muestra. Es el único
   poseedor de `RIGHT_EGRESS` (estado 5 del halo, "Nube").
3. Red en la imagen principal: la pila `net/` (N0) pasa de la imagen de prueba a
   la imagen normal, expuesta a Ring 3 por dos syscalls acotadas:
   `SYS_NET_SEND` y `SYS_NET_RECV` (UDP, un datagrama, ≤ 1280 bytes), con la
   capability nueva `CAP_NET` y derecho `RIGHT_EGRESS`.
4. Panel de chat (kernel, `console.e`): panel lateral izquierdo, translúcido,
   estilo WhatsApp, que no ocupa toda la pantalla. El resto de la pantalla sigue
   siendo el lienzo (fondo café) donde viven documentos y, más adelante, el 3D.
5. Conmutación de mente: el bucle de entrada envía la línea a `nube` si hay
   enlace; si no, a Qwen. El halo dice quién responde.

## Protocolo ELP1 (Erlan Link Protocol, versión 1)

UDP. En QEMU: guest 10.0.2.15 → host 10.0.2.2:4040 (SLIRP). En LAN física:
la IP de la pasarela, configurada en `limine.conf` como `ERLAN_GATEWAY=ip:puerto`.

Cabecera de 24 bytes, little-endian:

| off | tamaño | campo |
|---|---|---|
| 0 | u32 | magia `0x31504C45` ("ELP1") |
| 4 | u8 | versión = 1 |
| 5 | u8 | tipo |
| 6 | u8 | banderas: bit0 = último trozo |
| 7 | u8 | reservado = 0 |
| 8 | u32 | sesión (elegida por el cliente, ≠ 0) |
| 12 | u32 | secuencia del emisor (empieza en 1) |
| 16 | u32 | ack: última secuencia recibida del otro lado |
| 20 | u16 | longitud del cuerpo (≤ 1024) |
| 22 | u16 | reservado = 0 |

Tipos:

| tipo | nombre | dirección | cuerpo |
|---|---|---|---|
| 1 | HELLO | cliente → pasarela | 16 bytes de token + nombre del cliente (UTF-8) |
| 2 | HELLO_OK | pasarela → cliente | nombre del agente, p. ej. `claude-code 2.1.270` |
| 3 | USER | cliente → pasarela | trozo de texto del usuario; bit0 = fin del mensaje |
| 4 | REPLY | pasarela → cliente | trozo de respuesta; bit0 = fin de la respuesta |
| 5 | STATUS | pasarela → cliente | línea corta de estado: `lee kernel/src/pci.e`, `ejecuta make` |
| 6 | CANCEL | cliente → pasarela | vacío; el agente interrumpe el turno |
| 7 | PING | ambos | vacío |
| 8 | PONG | ambos | vacío |
| 9 | ACK | ambos | vacío; `ack` = secuencia confirmada |
| 10 | ERROR | pasarela → cliente | texto; también cierra la sesión |

Fiabilidad, deliberadamente simple para el lado Erlan: parada y espera por
sentido. Todo datagrama que no sea ACK se confirma con ACK. El emisor
retransmite a los 400 ms, hasta 5 veces; después el enlace se declara caído
y la mente pasa a Local. El receptor descarta secuencias repetidas. Un solo
datagrama en vuelo por sentido.

Seguridad de v1, declarada: token compartido de 16 bytes (en `limine.conf` y en
la pasarela). Sin cifrado. Sólo LAN o SLIRP. No apto para redes ajenas. La v2
llevará MAC con clave derivada; la v3, TLS nativo.

## Halo y panel

- Sin enlace: halo 0/1/2 como hoy, etiqueta `Local`.
- Con enlace y turno en curso: halo 5 (Nube) mientras se espera REPLY; sólo el
  servicio `nube` puede pedirlo porque sólo él posee `RIGHT_EGRESS`.
- Toda acción que el agente pida sobre el sistema (documentos, modo, panel)
  entra por el mismo broker `system` con recibo; el agente propone, el kernel
  garantiza. Ninguna respuesta de la nube se ejecuta como código.

Panel de chat (especificación para `console.e`):

- Lateral izquierdo, ancho = pantalla/3 (mínimo 400 px), desde y=72 (bajo la
  cabecera del halo) hasta el borde inferior. El resto de la pantalla es el
  lienzo y no se toca.
- Translúcido: se mezcla con el fondo al 85 % (alpha 217/255), calculado en el
  kernel por píxel; nada de "negro plano".
- Paleta WhatsApp oscuro: fondo del panel `#0B141A`, burbuja entrante `#202C33`,
  burbuja saliente `#005C4B`, texto `#E9EDEF`, acento `#00A884`, hora/estado
  `#8696A0`. Burbujas con margen 8 px, texto 8x16, ancho máximo 80 % del panel,
  salientes alineadas a la derecha, entrantes a la izquierda.
- Cabecera del panel: nombre de la mente (`Claude Code · Nube` o `Qwen · Local`)
  y estado (`escribiendo...`, `lee kernel/src/pci.e`).
- Línea de entrada al pie del panel con el prompt `>`.
- El panel es sólo pintura: el kernel recibe texto acotado y validado
  (UTF-8, ≤ 1024 bytes por burbuja, ≤ 64 burbujas en memoria) y decide píxeles.

## Automejora (ciclo v0, en QEMU)

1. El usuario pide un cambio en el chat.
2. El agente edita el repo en la pasarela, compila (`make all`) y prueba.
3. El agente produce la imagen nueva y envía STATUS `imagen lista`.
4. El usuario escribe `/actualizar`; la pasarela reinicia QEMU con la imagen
   nueva. En físico, esto exige persistencia, ranuras A/B y firma (M6/M7).

## Verificación

- `python scripts/gateway/test_gateway.py`: prueba de protocolo en loopback con
  el agente `echo` (saludo, trozos, ACK perdido y retransmisión, cancelación,
  token inválido).
- `python scripts/qemu_nube_test.py`: ErlanOS en QEMU contra la pasarela con
  `echo`; comprueba por serial `[nube] enlace ok agente=echo`, `[halo] estado=5`
  durante el turno y la burbuja en la captura; y sin pasarela, que Qwen responde.
- Con credenciales: `--agent claude` a mano, no en la suite automática.

## Límites conocidos

- Sin cifrado ni autenticación fuerte (token en claro).
- Sólo UDP; el trozo máximo es 1024 bytes; sin compresión.
- En el PC físico falta el controlador de la I219-V: hasta entonces la pasarela
  sólo corre en QEMU.
- Claude Code y Codex requieren sesión iniciada en la pasarela; ErlanOS nunca
  ve credenciales.
