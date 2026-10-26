# Estudio de controladores para esta PC

Fecha: 26 de octubre de 2026. Encargo del usuario: «usa REA para hacer o estudiar controladores
para llevarlos a REA después, como open source». Se interpreta como: **estudiar las referencias
de código abierto** de los controladores del hardware de esta PC para portarlos a Erlan más
adelante, dejando claro de dónde sale cada uno y con qué licencia. Sólo lectura de fuentes
públicas; no se descargó ni ejecutó nada. Si la intención era otra, se ajusta.

## 1. Inventario de esta PC

Obtenido en Windows el 26/10/2026 con `Get-PnpDevice -PresentOnly`,
`Get-CimInstance Win32_VideoController`, `Get-PhysicalDisk`, `Get-NetAdapter` y
`Win32_PnPSignedDriver`. Los IDs PCI son `vendor:device`.

| Componente | Dispositivo | ID | Nota |
|---|---|---|---|
| CPU | Intel Core i7-6700 (Skylake), 4 núcleos / 8 hilos | — | VT-x y EPT; Hyper-V activo en Windows |
| Placa | ASUS SABERTOOTH Z170 S, BIOS AMI 3801 (2018) | — | chipset Intel 100 Series (Sunrise Point, Z170) |
| RAM | 25,7 GB (24 GiB) | — | |
| Puente host / PEG | Skylake Host Bridge; PCIe x16 | `8086:191f`, `8086:1901` | |
| **GPU** | **NVIDIA GeForce RTX 3090 (GA102, Ampere)**, 24 GB | `10de:2204` (audio `10de:1aef`) | **Cambio respecto a HARDWARE-REAL.md** (14/09: GTX 1050 Ti `10de:1c82`). Driver Windows 32.0.16.1742 |
| **Almacenamiento NVMe** | Lexar NM790 4 TB («XG7000-4TB 2280»), DRAM-less | `1d97:1602` (Shenzhen Longsys) | Windows usa `stornvme` genérico |
| **Almacenamiento SATA** | Controlador AHCI del Z170 | `8086:a102` | discos: Kingston SUV400 240 GB (SSD), HGST 1 TB y Toshiba 1 TB (HDD) |
| **Red cableada** | Intel Ethernet Connection (2) I219-V | `8086:15b8` | familia e1000e, PHY en el PCH; cable desconectado al inventariar |
| Red inalámbrica | TP-Link «High Power Wireless USB Adapter» (TL-WN8200ND v2) | USB `2357:0126` | chip Realtek **RTL8192EU** (driver Windows de Realtek) |
| **USB** | xHCI Intel (USB 3.0) y xHCI ASMedia (USB 3.1) | `8086:a12f`, `1b21:1242` | |
| **Teclado y ratón** | USB HID: teclado `1a2c:2124`, ratón PixArt `093a:2533` | USB | **no hay teclado PS/2**: el `keyboard.e` actual sólo sirve si el BIOS emula PS/2 (USB Legacy) |
| Audio | Intel HD Audio del PCH; USB «AMR HD+» | `8086:a170`; USB `20b1:3009` | |
| Gestión | Intel MEI, SMBus, LPC, PMC | `8086:a13a`, `a123`, `a145`, `a121` | no hacen falta para un SO funcional |
| Puertos PCIe | root ports del PCH #1, #5, #9, #17 | `8086:a110`, `a114`, `a118`, `a167` | |

Lo que un SO funcional necesita aquí y hoy no existe en ErlanOS: **I219-V real, NVMe o AHCI,
USB xHCI con HID (sin él no hay teclado), y la GPU**.

## 2. Qué controlador tiene ErlanOS hoy

| Módulo | Qué hace | Dónde se probó |
|---|---|---|
| `kernel/src/pci.e` | Enumeración PCI por CONF1 (segmento 0), lectura de BAR, una sola escritura de Command (Memory Space + Bus Master) para la NIC elegida | QEMU, VirtualBox, **PC física** (G0, 18 funciones) |
| `kernel/src/e1000.e` | Intel 82540EM: reset, enlace, MAC de RAL/RAH, anillos RX/TX legacy, sondeo sin IRQ. Rechaza explícitamente 82574L e I219 (init de PHY/PCH no implementado) | QEMU (`-device e1000`), VirtualBox (82540EM emulado), con la pila `net/` hasta HTTP |
| `kernel/src/nvme.e` | NVMe (admin + una cola de E/S, Read/Write/Flush, MSI-X); escrito desde la especificación, con Linux leído como referencia | QEMU, VirtualBox |
| `kernel/src/i8042.e`, `keyboard.e`, `pointer.e` | Controlador i8042 (secuencia aprendida de Linux, escrito desde la referencia del 8042; sólo si la FADT dice que hay 8042), teclado y ratón PS/2 por el IOAPIC, scancodes set 1 | QEMU, VirtualBox |
| `kernel/src/storage.e` | VirtIO-blk **legado** (PCI `1af4:1001`), una cola, partición GPT propia «Erlan Workspace» | QEMU (`virtio-blk-pci,disable-modern=on`); en VirtualBox no hay VirtIO-blk |
| `kernel/src/fb.e`, `console.e` | Framebuffer lineal que entrega Limine (GOP de UEFI) | QEMU, VirtualBox, PC física |
| `kernel/src/serial.c`, `pic.e`, `pit.e`, `idt.e` | COM1, 8259, PIT a 100 Hz, IDT con `int 0x80` | todos |
| `kernel/src/vmm.e` (`vmm_map_mmio`) | Mapeo MMIO no cacheable (`PCD|PWT`) para BAR | QEMU, VirtualBox |

No hay: ACPI, IOAPIC/LAPIC, MSI/MSI-X, SMP, HPET, AHCI, NVMe, xHCI/USB, HID, driver de GPU,
audio, Wi-Fi.

## 3. Lo que falta, con su referencia abierta

Columna «Linux»: ruta en el árbol del kernel, **GPL-2.0**: una traducción a Erlan sería obra
derivada y obligaría a publicar ese módulo bajo GPL-2.0. Columna «Rust/permisivo»: código con
licencia MIT, BSD o Apache del que se puede traducir sin arrastrar copyleft (MPL-2.0 de
Asterinas: copyleft por archivo, el archivo traducido queda MPL). Las especificaciones del
hardware son públicas y son la fuente primaria: traducir la *especificación* no es traducir el
driver de nadie.

### 3.1 Red: Intel I219-V (`8086:15b8`)

| Referencia | Ruta | Licencia |
|---|---|---|
| Linux `e1000e` | `drivers/net/ethernet/intel/e1000e/`: `ich8lan.c` (PCH Sunrise Point, `e1000_pch_spt`, ID `E1000_DEV_ID_PCH_SPT_I219_V2 = 0x15B8`), `netdev.c`, `phy.c`, `nvm.c` | GPL-2.0 |
| FreeBSD `em` | `sys/dev/e1000/` (`e1000_ich8lan.c`, `if_em.c`): el mismo «código compartido» de Intel | **BSD-3** |
| DPDK `e1000/base` | `drivers/net/e1000/base/` («e1000/base: support more i219 devices») | **BSD-3** |
| Redox `e1000d` | `gitlab.redox-os.org/redox-os/drivers` → `net/e1000d` (82540EM y parientes) | MIT |
| rcore-os `e1000-driver` | Rust para 82540EP/EM y 82574L | por verificar |

Qué hay que añadir a `e1000.e`: el mapa de registros y los descriptores legacy ya coinciden; falta
el **init del PCH**: *handshake* con el ME/CSME (`FWSM`, `H2ME`), salida de ULP, acceso al PHY por
`MDIC` (y `SMBus` en estados de bajo consumo), `LTR`, *flash* NVM por `FLASH_BASE`, y después
**MSI** (requiere LAPIC/IOAPIC) en vez de sondeo. Peldaño intermedio: QEMU `-device e1000e`
(82574L) ejercita MDIC y MSI sin el PCH. Referencia primaria: el *I219 Datasheet* y el
*Intel Ethernet Controller I219 — Programming Interface* (públicos).

### 3.2 Almacenamiento NVMe: Lexar NM790 (`1d97:1602`)

**Hecho el 26/10/2026 (misma tarde): `kernel/src/nvme.e`**, ver [NVME.md](NVME.md): cola de
administración, Identify, una cola de E/S, Read/Write/Flush por PRP de una página, MSI-X.
Probado en QEMU (`1b36:0010`, D1 completa) y VirtualBox (`80ee:4e56`). Linux se leyó como referencia. Falta el NM790 físico.

| Referencia | Ruta | Licencia |
|---|---|---|
| Linux `nvme` | `drivers/nvme/host/pci.c`, `core.c`; *quirks* propuestos para este disco: `NVME_QUIRK_IGNORE_DEV_SUBNQN`, `NVME_QUIRK_BOGUS_NID` (NM790 2 TB); reportes «Device not ready, CSTS=0x0» | GPL-2.0 |
| Redox `nvmed` | `drivers` → `storage/nvmed` | MIT |
| Asterinas | driver NVMe en la capa de bloques (versión 0.18.0, junio de 2026) | MPL-2.0 |
| FreeBSD `nvme` | `sys/dev/nvme/` | BSD-2 |
| vroom (TUM) | driver NVMe de usuario en Rust, pequeño (tesis de T. Pirhonen, 2024) | por verificar (probablemente MIT) |
| Especificación | *NVM Express Base Specification* (nvmexpress.org) | pública |

Qué hace falta: BAR0 con `CAP`, `CC`, `CSTS`, `AQA/ASQ/ACQ`; cola de administración, `Identify`,
una cola de E/S, `Read`/`Write` con PRP de una página, *doorbells*; interrupción por MSI-X o
sondeo al principio. Es el driver más pequeño de la lista (la cola es una tabla en RAM). Peldaño:
QEMU `-device nvme`. Cuidado con el NM790: *quirks* de SUBNQN/NID y arranques lentos reportados.

### 3.3 Almacenamiento SATA: AHCI del Z170 (`8086:a102`)

**Hecho el 26/10/2026 (misma noche): `kernel/src/ahci.e`**, mecanismo AHCI en Erlan con un
puerto y un comando en vuelo por sondeo (READ/WRITE DMA EXT, FLUSH CACHE EXT, IDENTIFY), detrás
de la misma interfaz de bloques que VirtIO en `storage.e`. Probado con la aceptacion D1 completa
en QEMU (ICH9 `8086:2922`, `scripts/qemu_workspace_test.py --storage ahci`) y en VirtualBox
(ICH8 `8086:2829`, `scripts/vbox_erlanos.ps1 -Taller`, ejecución `build/vbox-erlanos/20261026-091503-qwen35`). El Z170 físico queda por probar.

| Referencia | Ruta | Licencia |
|---|---|---|
| Linux `ahci` | `drivers/ata/ahci.c`, `libahci.c`, `libata-core.c`, `libata-scsi.c` | GPL-2.0 |
| Redox `ahcid` | `drivers` → `storage/ahcid` | MIT |
| FreeBSD `ahci` | `sys/dev/ahci/` | BSD-2 |
| Especificación | *Serial ATA AHCI 1.3.1* (Intel) y *ATA/ATAPI Command Set* | públicas |

Qué hace falta: HBA en BAR5 (`CAP`, `GHC`, `PI`), por puerto `CLB/FB`, *command list* con
*command tables* y PRDT, FIS `H2D` con `READ/WRITE DMA EXT`, espera de `TFD.BSY/DRQ`. **Ventaja:
VirtualBox emula un AHCI ICH8 (`8086:2829`)**: se puede probar el driver en la VM de
`scripts/vbox_erlanos.ps1` antes de tocar el disco físico, y el SSD Kingston de 240 GB es un
destino natural para instalar ErlanOS sin tocar Windows (que vive en el NVMe).

### 3.4 USB: xHCI (`8086:a12f`, `1b21:1242`) y HID

| Referencia | Ruta | Licencia |
|---|---|---|
| Linux `xhci-hcd` | `drivers/usb/host/xhci.c`, `xhci-ring.c`, `xhci-mem.c`, `xhci-pci.c`; HID: `drivers/hid/usbhid/hid-core.c`, `drivers/hid/hid-input.c` | GPL-2.0 |
| Redox `xhcid`, `usbhidd` | `drivers` → `usb/xhcid` (marcado como incompleto en el seguimiento de Redox), `usb/usbhidd` | MIT |
| rust-osdev `xhci` | *crate* de accesores de registros, anillos y TRB (`github.com/rust-osdev/xhci`, v0.9.x) | **MIT OR Apache-2.0** |
| Especificaciones | *eXtensible Host Controller Interface 1.2* (Intel), *USB 2.0/3.2*, *HID 1.11* y *HID Usage Tables* | públicas |

Qué hace falta: es el driver más grande de los necesarios: *capability/operational/runtime
registers*, DCBAA, *command ring*, *event ring* (necesita interrupciones o sondeo del ER), *slots* y
*endpoint contexts*, enumeración (`Enable Slot`, `Address Device`, descriptores), y encima HID
*boot protocol* para teclado y ratón. Sin esto, en esta PC **no hay teclado** salvo que el BIOS
mantenga la emulación PS/2 (USB Legacy Support, que suele apagarse al iniciar un xHCI). Por
eso va antes que la GPU.

### 3.5 GPU: NVIDIA RTX 3090 (GA102, `10de:2204`)

| Referencia | Ruta | Licencia |
|---|---|---|
| NVIDIA *open-gpu-kernel-modules* | `github.com/NVIDIA/open-gpu-kernel-modules` (`kernel-open/`, `src/nvidia/`); cubre **Turing, Ampere, Ada, Hopper, Blackwell**: la 3090 **sí** está cubierta (la 1050 Ti no lo estaba) | **dual MIT / GPL-2.0** |
| Linux `nova-core` (Rust) | `drivers/gpu/nova-core/` (en el árbol desde 6.15; GSP en Turing–Blackwell con «funcionalidad básica», aún no apto para usuarios según LPC 2026) | GPL-2.0 |
| Linux `nouveau` | `drivers/gpu/drm/nouveau/` (Ampere vía GSP-RM) | GPL-2.0/MIT por archivo |
| Mesa NVK | espacio de usuario Vulkan para NVIDIA | MIT |
| Firmware GSP | `linux-firmware` → `nvidia/ga102/gsp/` | propietario redistribuible (licencia de firmware de NVIDIA), no es código abierto |

Qué significa para ErlanOS: a diferencia del 14/09 (Pascal sin módulos abiertos), ahora hay
código abierto **permisivo** (MIT) que inicializa esta GPU, pero depende de cargar el firmware
GSP-RM en el *GPU System Processor* y hablar con él por RPC: el «driver» es sobre todo un
cliente del firmware. Es meses de trabajo, y el cómputo (CUDA) sigue siendo espacio de usuario
cerrado; el camino práctico para la IA en la 3090 sigue siendo la base Linux de
[LINUX-BASE.md](LINUX-BASE.md). Para el kernel propio: primero leer `src/nvidia/arch/nvalloc/`
y `kernel-open/nvidia/nv-pci.c` (MIT) y el *boot* de GSP en `nova-core` (GPL, sólo lectura).

### 3.6 Lo demás

| Pieza | Para qué | Referencias |
|---|---|---|
| ACPI (tablas MADT, MCFG, HPET, FADT) | encontrar IOAPIC/LAPIC, PCIe ECAM, HPET, apagado | ACPICA (`drivers/acpi/acpica/`, **dual BSD-3/GPL-2.0**), *crate* `acpi` de rust-osdev (MIT/Apache), *ACPI 6.5* pública |
| LAPIC/IOAPIC, MSI/MSI-X, x2APIC | interrupciones de I219, NVMe y xHCI; SMP (8 hilos) | *Intel SDM* vol. 3 (público); Linux `arch/x86/kernel/apic/` (GPL); Redox `kernel/src/arch/x86_64/device/` (MIT) |
| PCIe ECAM | configuración extendida (hoy sólo CONF1, 256 bytes) | *PCI Express Base* y MCFG; `pci_types` (rust-osdev, MIT/Apache) |
| HD Audio (`8086:a170`) | sonido, opcional | Linux `sound/pci/hda/` (GPL); Redox `audio/ihdad` (MIT); *Intel HDA 1.0a* pública |
| Wi-Fi RTL8192EU (USB `2357:0126`) | red sin cable | Linux `drivers/net/wireless/realtek/rtl8xxxu/` (GPL; el ID 0126 puede faltar en la tabla) y forks de Realtek (GPL); necesita USB + pila 802.11: **no vale la pena antes que el cable** |
| PS/2 i8042 | hecho el 26/10/2026 (`i8042.e`, [APIC.md](APIC.md)) | Linux `drivers/input/serio/i8042.c` (GPL, **leído**, no traducido); Redox `input/ps2d` (MIT) |

## 4. Licencias: regla para el repo

- **Leer** cualquier código abierto para entender el hardware está bien y conviene (Linux es el
  mapa más completo de *quirks*). **Traducir** líneas de un driver GPL-2.0 produce obra derivada:
  ese módulo de ErlanOS tendría que ser GPL-2.0. SO-Erlan no se publica directo, pero
  `Erlansoo/ErlanOS` sí, y ahí la licencia de cada módulo cuenta.
- Para traducir, usar las **especificaciones** y el código **MIT/BSD/Apache** (Redox, FreeBSD,
  DPDK base, rust-osdev, NVIDIA open modules bajo MIT), y dejar en cada `.e` una cabecera con la
  fuente, su commit y su licencia, como ya hace `pci.e` («encodings cross-checked against Linux
  …; implementation is original Erlan») y como hizo `build/gpu-research/20260914/manifest.json`.
- Asterinas (MPL-2.0) se puede traducir si el archivo resultante queda MPL-2.0; es menos cómodo
  que MIT y no aporta drivers que esta PC necesite (su NVMe es la excepción).
- Los **firmwares** (GSP de NVIDIA, y los de Wi-Fi) son binarios con licencia propia: se
  distribuyen, no se traducen.

## 5. Orden de portado propuesto

Cada paso termina con una prueba reproducible (QEMU y, donde se pueda, VirtualBox) antes de la PC.

1. **ACPI mínimo + LAPIC/IOAPIC + MSI.** Hecho el 26/10/2026 ([APIC.md](APIC.md)): RSDP,
   XSDT, MADT, MCFG y HPET leídos y verificados; LAPIC habilitado y su temporizador, calibrado
   contra el PIT, es el reloj del sistema; IOAPIC inventariado; MSI funcionando en el AHCI
   (vector 49) en QEMU y VirtualBox. Teclado y ratón **por el IOAPIC** (IRQ1 → 33, IRQ12 → 44,
   8259 apagado) y controlador `i8042.e` con la bandera 8042 de la FADT, hechos la misma tarde.
   Faltan SMP y el ECAM.
2. **AHCI**: hecho en QEMU y VirtualBox (`ahci.e`, ver 3.3); falta la prueba en el Z170 físico
   con el SSD Kingston y la imagen de `scripts/flash_usb.ps1`. Da persistencia real en esta PC.
3. **NVMe.** Hecho el 26/10/2026 (`nvme.e`, [NVME.md](NVME.md)) en QEMU y VirtualBox, con MSI-X.
   En la PC, sólo lectura del NM790 hasta tener confianza (ahí vive Windows).
4. **I219-V**: extender `e1000.e` con el init del PCH y MSI, traduciendo del código compartido
   de Intel en FreeBSD/DPDK (BSD-3) y leyendo `ich8lan.c` para los *quirks*. Peldaño QEMU
   `-device e1000e`. Prueba: `make test-net` por cable en la PC. Con esto la pila `net/` ya
   probada en VirtualBox llega a internet en hardware real.
5. **xHCI + HID** (teclado y ratón USB), con el *crate* `xhci` de rust-osdev (MIT/Apache) como
   referencia de registros y Redox `usbhidd` para HID. Prueba: QEMU `-device qemu-xhci` con
   `usb-kbd`/`usb-mouse`, VirtualBox con xHCI (necesita el *Extension Pack* para USB 3; USB 2
   OHCI/EHCI vienen de serie), luego la PC.
6. **HD Audio** (opcional, pequeño con la especificación).
7. **GPU GA102**: estudio del *boot* de GSP en los módulos abiertos de NVIDIA (MIT) y en
   `nova-core`; cualquier port es un proyecto aparte y la inferencia con CUDA sigue por la base
   Linux.

**PC vieja (Core i3)**: sin inventario todavía. Cuando esté a mano, repetir la sección 1 allí;
lo normal en esa generación es AHCI (no NVMe), e1000e o Realtek `r8169` (GPL; Redox tiene
`rtl8168d`, MIT), EHCI/xHCI y PS/2 real, que es justo lo que este orden cubre primero.

## Fuentes consultadas (26/10/2026)

- Redox OS, repositorio `drivers` y seguimiento de drivers incompletos: <https://gitlab.redox-os.org/redox-os/drivers>, <https://code.redox-os.org/redox-os/base/issues/56>; licencia MIT según el espejo indexado en <https://oosmetrics.com/repo/redox-os/drivers>.
- Asterinas 0.18.0 (NVMe) y licencia MPL: notas de versión y README en el espejo <https://cdn09022024.gitlink.org.cn/duckran/asterinas/commit/866d050736f3c19ffe3e8cba759fffa50c138e4e>.
- Nova (Rust, NVIDIA GSP): <https://www.phoronix.com/news/NOVA-Driver-For-Linux-6.15>, <https://www.phoronix.com/news/NVIDIA-Turing-Nova-Core-GSP>, <https://www.phoronix.com/news/Nova-Driver-LPC-2026>, <https://rust-for-linux.com/nova-gpu-driver>.
- NVIDIA open GPU kernel modules (Turing+, dual GPL/MIT): <https://developer.nvidia.com/blog/nvidia-releases-open-source-gpu-kernel-modules>, <https://developer.nvidia.com/blog/nvidia-transitions-fully-towards-open-source-gpu-kernel-modules>, <https://download.nvidia.com/XFree86/Linux-x86_64/560.35.03/README/kernel_open.html>.
- TP-Link 2357:0126 = TL-WN8200ND v2 con RTL8192EU: <https://github.com/luckynzm/tlwn8200nddriver>, <https://github.com/kimocoder/rtl8192eu>, <https://bbs.archlinux.org/viewtopic.php?id=219909>.
- Lexar NM790 `1d97:1602` (Longsys) y *quirks* NVMe: <https://lists.infradead.org/pipermail/linux-nvme/2024-May/047563.html>, <https://linux.org/goto/post?id=203337>.
- e1000e / Sunrise Point i219 en `ich8lan.c` y `e1000.h`: <https://git.sysmocom.de/debian-packaging/linux/commit/ceb687e6084744a137683cc2e5975f4264a6176e>, <https://lab.nexedi.com/kirr/linux/-/commit/048b6ea9c9d7737a01d17787d84b75e6007f399d>, DPDK i219: <https://patches.dpdk.org/project/dpdk/patch/1479921780-9813-15-git-send-email-wenzhuo.lu@intel.com/>.
- *crate* `xhci` (MIT OR Apache-2.0): <https://github.com/rust-osdev/xhci>, <https://docs.rs/crate/xhci/latest>.
- vroom (NVMe en Rust, TUM): <https://db.cit.tum.de/~ellmann/theses/finished/24/pirhonen_writing_an_nvme_driver_in_rust.pdf>, <https://deepwiki.com/bootreer/vroom>.
- Theseus OS (MIT) y rcore-os `e1000-driver`: <https://github.com/theseus-os/Theseus>, <https://github.com/rcore-os/e1000-driver>.
