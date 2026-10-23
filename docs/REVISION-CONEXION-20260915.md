# Revisión del trabajo de conexión — 15 de septiembre de 2026, 10:18 Bolivia

Revisión de archivos y evidencias; no modifica las fuentes de red de Claude.

## Confirmado

- `build/net-test/20260915-021829-498dbkp5/result.json` registra ARP, DHCP, DNS,
  transferencia TCP de 6000 bytes de subida y 300000 de bajada, con el mismo SHA-256
  calculado en huésped y anfitrión. La petición HTTP a example.com obtuvo 200 OK.
  Es una prueba en QEMU con e1000, no una prueba de la I219-V física.
- Repetí la suite criptográfica en una copia aislada: **415/415 vectores**.
  Incluye SHA-2, HMAC/HKDF, AES-GCM, X25519 y ECDSA. Es evidencia de las primitivas
  matemáticas, no de un handshake HTTPS completo ni una auditoría de seguridad.
- Repetí la pasarela en loopback con `echo`: token inválido, saludo, fragmentación,
  ACK perdido/retransmisión, duplicados y cancelación pasaron.
- Hay adaptadores Python para ejecutar `claude -p` y `codex exec` en el anfitrión.

## Todavía no demostrado en el árbol revisado

- No hay un servicio `services/nube` ni una prueba `qemu_nube_test.py` implementados.
  Aparecen en la propuesta, pero no equivalen a código ejecutable.
- Hay anclas de confianza y primitivas cripto, pero no encontré aún el handshake
  TLS completo ni la validación completa de cadenas X.509 y nombre de servidor.
- El último log de prueba con el CLI real, `build/gateway/claude-01.log`, termina
  en `ConnectionResetError: WinError 10054`; no acredita un turno completo.
- No hay evidencia revisada de conversación ErlanOS → Claude/Codex → ErlanOS.

## Distinción de arquitectura para el objetivo del usuario

TCP/TLS/HTTP nativos permiten conexiones seguras directas. No proporcionan el entorno
de ejecución que necesitan los CLI de Claude Code o Codex. En la pasarela actual,
estos programas siguen ejecutándose en Windows y usan su sesión del anfitrión.
Una integración directa con una API y una integración con el CLI son trabajos
distintos; conviene definir cuál se considera terminado antes de presentar el hito.

El adaptador Codex usa `resume --last`: antes de conectarlo con proyectos, debería
reanudar un ID de sesión propio de cada proyecto, evitando depender de la última
sesión del directorio. Esta observación es por lectura del código, sin ejecutar
sesiones reales ni consumir credenciales de los proveedores.

Próxima evidencia necesaria: un turno real completo desde el chat del huésped,
con proveedor identificado, respuesta, cancelación y recuperación de conexión;
después, repetirlo en hardware físico cuando exista su controlador de red.
