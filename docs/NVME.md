# NVMe — ErlanOS lee y escribe un SSD NVMe

Hito del 26 de octubre de 2026 (paso 3 del orden de portado de
[DRIVERS-ESTUDIO.md](DRIVERS-ESTUDIO.md)). Todo en Erlan: `kernel/src/nvme.e` (nuevo),
`pci_msi.e` (MSI-X), `pit.e` (`pit_sleep_ms`), y los cambios en `storage.e`, `idt.e`, las
pruebas QEMU (`--storage nvme`) y el script de VirtualBox (`-Taller -Nvme`). Especificaciones:
*NVM Express Base 2.0* (registros §3.1, colas §3.3, entradas §4.1, administración §5) y *NVM
Command Set 1.0* (Read, Write, Flush); PCI 3.0 §6.8.2 para MSI-X. Linux se
estudió como referencia, no se copió: ver «Qué enseñó Linux».

## Qué hace

1. **Encuentra el controlador** (clase PCI `01.08.02`), habilita memoria y bus master y mapea
   BAR0 sin caché (registros + timbres; más páginas si `CAP.DSTRD` lo pide).
2. **Lo reinicia y lo habilita** como manda la especificación y con los plazos que usa Linux:
   `CSTS = 0xFFFFFFFF` es «no hay dispositivo»; `CAP.MPSMIN` debe ser 4 KiB; `CC.EN = 0` y
   espera `CSTS.RDY = 0`; AQA, ASQ y ACQ (cola de administración de 32 entradas, una página
   cada una); `CC` sin `EN` (CSS = NVM, MPS = 0, AMS = RR, IOSQES = 6, IOCQES = 4) y luego con
   `EN`; espera `CSTS.RDY = 1` sondeando cada 1 ms (`pit_sleep_ms`, canal 2 del PIT) hasta
   `(CAP.TO + 1) × 500 ms`; `CSTS.CFS` corta.
3. **Identify Controller** (CNS = 1, buffer de 4 KiB): NN, MDTS, VWC, SQES/CQES por serial.
   **Identify Namespace** (CNS = 0, NSID = 1): NSZE, NCAP, FLBAS y la entrada LBAF elegida; por
   ahora sólo sectores de 512 bytes sin metadatos (lo que dan QEMU, VirtualBox y el NM790 de
   fábrica); otro formato se dice y se rechaza.
4. **Una cola de E/S**: Set Features *Number of Queues* (una), Create I/O CQ 1 (contigua, con
   interrupción, vector 0) y después Create I/O SQ 1 (contigua, CQID 1), en ese orden.
5. **Read / Write / Flush** por LBA, un sector por orden, PRP1 = una página de DMA, sin PRP2 ni
   lista. La completación se sondea por el **bit de fase** de la entrada de 16 bytes; se
   comprueba el CID; el timbre de la CQ es el acuse. La política (partición GPT «Erlan
   Workspace», qué proceso) sigue en `storage.e`, que ahora prueba VirtIO, luego NVMe, luego
   AHCI.
6. **MSI-X** (`pci_enable_msix`, nuevo): la tabla vive en el BAR que dice la capacidad (BIR +
   offset); se mapea su página, se escribe la entrada 0 (dirección del LAPIC, dato = vector 50,
   sin máscara) y se pone Enable sin Function Mask. Si no hay MSI-X se intenta MSI; si no, se
   sigue por sondeo. Las interrupciones se cuentan (`[nvme] primera interrupcion por el vector
   50`, `flush ... interrupciones=N`); las esperas siguen siendo por sondeo, como en el AHCI.

## Qué se midió

| Máquina | Controlador | Identify | Colas | Interrupciones | Prueba |
|---|---|---|---|---|---|
| QEMU q35 (OVMF) | `1b36:0010`, NVMe 1.4, MQES 2048, DSTRD 0, TO 8 s, CSS `0x41` | NN 256, MDTS 7 (512 KiB), VWC; ns 1: 792 609 sectores de 512 B, sin metadatos | admin 32 + E/S 32 | MSI-X: capacidad en `0x40`, 65 entradas, tabla en BAR0+0x2000; vector 50 entregado (`primera interrupcion`, 10–11 por fase) | D1 completa por NVMe: `workspace-test/20261026-105900-_uijmisg` y `-105949-vskhqu9m` (objetos persistidos, commit 1 → 3, re-arranque) |
| VirtualBox (EFI) | `80ee:4e56` (VirtualBox), NVMe 1.2, MQES 4096, DSTRD 0, TO 5,5 s, CSS `0x1` | NN 1, MDTS 0 (sin límite), sin VWC; ns 1: 4 339 745 sectores de 512 B, sin metadatos | admin 32 + E/S 32 | el controlador no anuncia MSI-X ni MSI: completaciones por sondeo | taller montado por NVMe y `/estado` tecleado por PS/2 respondido en 1,2 s (`vbox-erlanos/20261026-110113-qwen35`) |

## Qué enseñó Linux (y qué no se copió)

`drivers/nvme/host/pci.c`, `core.c` y `nvme.h` son GPL-2.0: se leyeron para la secuencia y las
rarezas, y el código se escribió desde la especificación. Lo aprendido y aplicado:

- `CSTS == 0xFFFFFFFF` significa dispositivo ausente; `CAP` se vuelve a leer tras escribir `CC`
  («CAP value may change after initial CC write»); `CAP.MPSMIN` mayor que la página del host es
  motivo de rechazo (`-ENODEV`), y `MPSMAX` no se mira.
- Profundidad de cola = `min(CAP.MQES + 1, 1024)`; Linux usa 1024, ErlanOS 32 (un comando en
  vuelo). Timbres en `0x1000 + qid × 2 × (4 << DSTRD)` (SQ) y la entrada siguiente (CQ).
- `CC` se escribe **sin** `EN` con CSS, MPS, AMS, IOSQES = 6 e IOCQES = 4, y después con `EN`;
  la espera de `CSTS.RDY` sondea cada 1–2 ms con plazo `(CAP.TO + 1) / 2` s; `CSTS.CFS` se
  consulta para decidir un reinicio. Rareza `NVME_QUIRK_DELAY_BEFORE_CHK_RDY` (2300 ms antes de
  sondear, Samsung PM1725 y X5): no aplicada, no hay ninguno de esos aquí.
- La CQ de E/S se crea **antes** que la SQ (y se borran SQ primero, CQ después); el CID lleva
  un contador de generación en los bits 15:12 para detectar completaciones viejas (aquí basta
  comprobar el CID, con un comando en vuelo).
- PRP: PRP1 puede estar desalineado; PRP2 es un puntero directo si el resto cabe en una
  página y una lista si no; un sector de 512 B dentro de una página sólo necesita PRP1.
- Lexar NM790 (`1d97:1602`): no está en la tabla de rarezas de Linux; la propuesta de 2024
  (`BOGUS_NID | IGNORE_DEV_SUBNQN`) sólo calla un aviso, según Keith Busch «everything works the
  same with or without the quirk». QEMU `1b36:0010` lleva `BOGUS_NID` (identificadores de
  espacio de nombres inventados): no nos afecta, no leemos EUI64/NGUID.
- Lo que Linux hace y ErlanOS no (todavía): una cola por CPU con MSI-X por cola, SGL,
  listas PRP, APST (gestión de energía), HMB, apagado ordenado con `CC.SHN` y espera de
  `CSTS.SHST`, reinicio del subsistema (NSSR), sondeo de todos los espacios de nombres.

## Qué no hace todavía

- Sólo sectores de 512 bytes sin metadatos (4Kn se rechaza con mensaje).
- Un comando en vuelo y una sola cola de E/S; sin listas PRP (un sector por orden).
- Sin apagado ordenado (`CC.SHN`): al reiniciar la VM el controlador se reinicia con `EN = 0`.
- Sin prueba en el Lexar NM790 físico: es el siguiente paso con `scripts/flash_usb.ps1`.

## Cómo se comprobó

- QEMU: `scripts/qemu_workspace_test.py --storage nvme` (aceptación D1 por NVMe con MSI-X), y
  las regresiones `--storage ahci` y VirtIO por defecto.
- VirtualBox: `scripts\vbox_erlanos.ps1 -Perfil qwen35 -Taller -Nvme`.
- `make test` en verde.
