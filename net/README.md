# net — pila de red de ErlanOS en Erlan

Módulos independientes de plataforma. Sólo dependen de `netplat.h` (tx/rx de tramas,
reloj, log, consola), que hoy provee el kernel de prueba (`kernel/src/netplat.h`
sobre `e1000.e`) y mañana proveerá un servicio Ring 3 sobre `SYS_NET_TX/RX`.

| Módulo | Contenido |
|---|---|
| `wire.e`    | big-endian `be16/be32`, `inet_sum/inet_csum` (RFC 1071), utilidades MAC, formateo para logs |
| `arp.e`     | construcción y análisis de ARP request/reply sobre Ethernet II |
| `ipv4.e`    | IPv4 (sin fragmentación) y UDP con checksums; análisis a globales del módulo |
| `netio.e`   | buffers, caché ARP, resolución con reintento, siguiente salto, envío UDP y espera por sondeo |
| `dhcp.e`    | cliente DHCP: DISCOVER → OFFER → REQUEST → ACK con opciones 1/3/6/51/53/54/55 |
| `dns.e`     | cliente DNS: consulta A, punteros de compresión, CNAME hasta el A, reintentos |
| `netmain.e` | orquestador de la prueba N0 con marcadores verificables en serial y consola |

Capa por capa: `wire < arp < ipv4 < netio < dhcp, dns < netmain`. Las IPs viajan como `u32`
en orden de host y se escriben al cable con `be32_put`; los structs de cabecera son la
especificación por offsets: nunca se superpone un puntero a struct sobre el paquete.

Verificación: `make test-net` (ver `docs/RED.md`).
