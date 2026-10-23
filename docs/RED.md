# Red — N0: ErlanOS en internet (QEMU)

Hito verificado el 14 de septiembre de 2026. ErlanOS, con su propio kernel, obtiene una
dirección por DHCP y resuelve un nombre público a través del resolver real del host.
Corre en QEMU con red de usuario (SLIRP), que hace NAT hacia la conexión de Windows.
No requiere USB, virtualización en BIOS ni arranque físico.

## Qué hay

| Pieza | Archivo | Papel |
|---|---|---|
| Accesos MMIO volátiles y barreras | `kernel/src/mmio.h` | `mmio_read/write*`, `mfence`, `pause`; evita que clang eleve o reordene accesos a registros y descriptores |
| Escritura PCI acotada | `kernel/src/pci_io.h`, `kernel/src/pci.e` | `pci_conf1_write32` sólo la usa `pci_enable_bus_master` (bits Memory Space y Bus Master del Command del NIC identificado por el kernel). Auditada en `tests/pci/shim_audit.py` (exactamente una escritura a CFC) y probada en `tests/pci/pci_host.c` |
| Captura del NIC | `kernel/src/pci.e` | El primer Intel 8086:100E / 10D3 / 15B8 con BAR0 de memoria asignado: BDF, BAR0, IRQ |
| Mapeo MMIO no cacheable | `kernel/src/vmm.e` | `vmm_map_mmio` con `PTE_PCD\|PTE_PWT` en la mitad de kernel (TCG no modela caché: sólo el hardware real lo exige) |
| Driver e1000 | `kernel/src/e1000.e` | 82540EM: reset, SLU/ASDE, MAC de RAL0/RAH0, anillos RX/TX de 32 descriptores en una página, buffers de 2 KiB, TX síncrono con RS/DD, RX por sondeo, RDT/TDT con `mfence` |
| Plataforma | `kernel/src/netplat.h` | tx/rx/reloj/log/consola para los módulos de `net/` dentro del kernel de prueba |
| Pila en Erlan | `net/wire.e`, `arp.e`, `ipv4.e`, `netio.e`, `dhcp.e`, `dns.e`, `netmain.e` | Orden de red, checksums, ARP con caché y respuesta, IPv4+UDP con checksums, cliente DHCP DORA, cliente DNS con punteros de compresión, orquestador |
| Imagen y prueba | `make net-image`, `make run-net`, `make test-net`, `scripts/qemu_net_test.py` | Imagen privada sin modelo; QEMU con `-netdev user` + `-device e1000` + `filter-dump` (pcap) |

La pila se enlaza dentro del kernel de prueba (`kernel-net.elf`, `ERLANOS_NET_PROBE`)
para llegar al hito con cero primitivas nuevas de usuario. El kernel normal incluye
`e1000.e` y la captura PCI pero no inicializa la NIC: el arranque por defecto no cambia.

## Verificación (tres oráculos independientes)

1. **Serial del guest**: `[net] arp ok gw=10.0.2.2 mac=52:55:0a:00:02:02`,
   `[net] dhcp ok ip=10.0.2.15 mask=255.255.255.0 gw=10.0.2.2 dns=10.0.2.3 lease=86400s`,
   `[net] dns ok example.com=<IP>`, `[net] INTERNET OK`.
2. **pcap de QEMU** (`net.pcap` en `build/net-test/<run>/`): peticiones y respuestas ARP,
   Discover/Offer/Request/Ack y consulta/respuesta DNS, con contadores en `result.json`.
3. **Resolver del host**: la IP del guest debe pertenecer al conjunto que devuelve
   `getaddrinfo("example.com")` en Windows. Si el host no tiene internet, la prueba falla
   con ese motivo, no con un falso positivo.

Primera ejecución: 16 tramas, DHCP en el primer intento, DNS en 210 ms bajo TCG.

## Decisiones (de la revisión adversarial del diseño)

- e1000 (82540EM) y no virtio-net: mismo mapa de registros y descriptores legacy que la
  familia e1000e del I219-V físico; virtio no transfiere nada al hardware real.
- MAC de RAL0/RAH0 en vez de EEPROM: portátil a e1000e/I219 (EERD cambia de codificación).
- Sondeo y no IRQ: en q35 la INTx del PCI va por el PIRQ/IOAPIC; el kernel es 8259. Las
  interrupciones llegan como optimización medida, no como requisito.
- ICMP externo no es prueba de internet en SLIRP sobre Windows; DNS y TCP sí.
- Anillos de una página: 32 x 16 bytes; sin asignador contiguo nuevo.

## Lo que NO es todavía

- **No es soporte del I219-V físico.** Comparte registros, pero el I219 exige el handshake
  con ME/CSME, ULP, PHY por MDIC y MSI (que necesita APIC). Ese es un hito propio.
- **No hay TCP** ni HTTP: es el siguiente paso sobre esta base.
- **No hay TLS.** Evaluación honesta: TLS 1.3 + X.509 nativos en Erlan son un proyecto largo
  y peligroso de auditar. Para investigar HTTPS antes de eso, el camino declarado es un
  proxy del lado host (no nativo, explícito). No condicionar "la IA investiga" a TLS nativo.
- **La pila corre en el kernel de prueba**, no en un servicio Ring 3. La forma final:
  `SYS_NET_TX/RX` con copia acotada tras `CAP_NIC`, y el mismo código `net/` en el servicio.
- Sin caché coherente modelada: un `PTE_PCD` ausente o una barrera faltante pasan en QEMU
  y fallan en hardware real. El código ya los incluye, pero QEMU no los valida.

## Próximos pasos en orden

1. TCP mínimo (cliente: SYN, ventana fija, retransmisión, FIN) y `GET / HTTP/1.0` a un
   host real, con el `200 OK` visible en serial y pcap.
2. Servicio `net` en Ring 3: `CAP_NIC`, `SYS_NET_TX/RX`; los módulos `net/` sin cambios.
3. Endpoint IPC para que L3 pida "resolver" y "GET" con recibo; el contenido recuperado es
   dato, nunca autoridad.
4. Peldaño e1000e en QEMU (`-device e1000e`): MSI y PHY por MDIC, camino al I219-V.
5. Proxy host declarado para HTTPS; TLS nativo como pista experimental posterior.
