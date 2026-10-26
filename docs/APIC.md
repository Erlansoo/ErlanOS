# ACPI, APIC y MSI — ErlanOS deja el PIT y recibe interrupciones por mensaje

Hito del 26 de octubre de 2026 (paso 1 del orden de portado de
[DRIVERS-ESTUDIO.md](DRIVERS-ESTUDIO.md)). Todo en Erlan: `kernel/src/acpi.e`, `apic.e`,
`pci_msi.e`, `i8042.e`, y los cambios en `ahci.e`, `idt.e`, `pic.e`, `keyboard.e`, `pointer.e`,
`boot.e`, `main.e` y las entradas de prueba. Sólo especificaciones públicas: ACPI 6.5, Intel SDM
vol. 3A cap. 11, 82093AA I/O APIC, PCI 3.0 §6.8, referencia del 8042 (IBM PC AT, Intel 8042).
Linux se **leyó** (ver «Qué enseñó Linux»); no se tradujo.

## Qué hace

1. **ACPI mínimo** (`acpi.e`). Limine entrega el RSDP (petición `rsdp` del protocolo, física
   desde la revisión base 3). Se verifican las sumas del RSDP, de la XSDT (o RSDT) y de cada
   tabla, se listan todas por serial y se leen tres: **MADT** (dirección del LAPIC, CPUs con su
   APIC id, IOAPICs con su base de GSI, redirecciones de IRQ ISA, compatibilidad con el 8259),
   **MCFG** (base ECAM de PCIe, para la configuración extendida que vendrá) y **HPET** (base).
   No ejecuta AML ni escribe nada. Se lee **antes** de `vmm_init`: las tablas viven en memoria
   `acpi-reclaim`/`acpi-nvs`, que el HHDM de Limine mapea y las tablas del kernel todavía no
   (el primer intento después de `vmm_init` terminó en `#PF` en `cr2=0xffff80007fb7e015`).
2. **LAPIC** (`apic.e`). Comprueba `IA32_APIC_BASE`, mapea el LAPIC sin caché, lo habilita
   (SVR con vector espurio 255, TPR 0), imprime id y versión, y lee el IOAPIC (id, versión,
   número de entradas).
3. **Temporizador del LAPIC como reloj del sistema.** Se calibra contra el canal 2 del PIT en
   modo de un disparo (10 ms, sin interrupciones), se programa periódico a 100 Hz por el vector
   48, y se enmascara la IRQ0 del PIC. El vector 48 llama a lo mismo que llamaba la IRQ0
   (`pit_tick` + `sched_tick`), con EOI al LAPIC. Si la calibración no da una cuenta útil, el
   PIT sigue mandando: no hay regresión posible por esa vía.
4. **MSI** (`pci_msi.e`). Recorre la lista de capacidades del dispositivo, programa la
   dirección (`0xFEE00000 | apic_id << 12`) y el dato (vector) del mensaje y habilita MSI con un
   vector. El primer usuario es el **AHCI**: vector 49, con las fuentes del puerto (`PxIE`) y
   del HBA (`GHC.IE`) activas. El driver sigue esperando por sondeo de `PxCI`, pero cada
   completación llega además por MSI al LAPIC y a la IDT, y se cuenta: la línea
   `[ahci] flush ok=1 interrupciones msi=N` de cada `flush` del espacio de trabajo es la prueba.
   Si el dispositivo no anuncia la capacidad (hay emuladores sin ella), se dice y se sigue por
   sondeo.
5. **Teclado y ratón por el IOAPIC; el 8259 apagado** (`apic_switch_to_ioapic`, 26/10 por la
   tarde). Tras arrancar el reloj del LAPIC se enmascaran las 24 entradas del IOAPIC y las 16
   líneas del PIC, se deja LINT0 enmascarado (el «cable virtual» por el que el 8259 llegaba al
   LAPIC), y se rutan **IRQ1 → vector 33** e **IRQ12 → vector 44** con la polaridad y el disparo
   que dicen las redirecciones de la MADT (`acpi_irq_to_gsi`, `acpi_irq_flags`; sin
   redirección: identidad, flanco, activo alto, como manda el bus ISA). Cada entrada se escribe
   alta (destino) y luego baja (vector y modos), para que nunca quede desenmascarada a medias.
   El EOI de esas dos interrupciones va al LAPIC, no al PIC (`idt.e` mira `apic_ioapic_mode`).
   Si no hay LAPIC o IOAPIC (`probe_hardware`), todo sigue por el 8259 como antes.
6. **Controlador i8042 propio** (`i8042.e`): el mecanismo PS/2 que antes estaba repartido
   entre `keyboard.e` y `pointer.e`. Sólo se toca si la **FADT** (`IAPC_BOOT_ARCH`, bit 1) dice
   que hay 8042, o si la FADT es anterior a ACPI 2.0 (entonces se asume, como Linux): en la PC
   física con teclado USB y sin emulación, no se escribe en `0x60/0x64` a ciegas. Secuencia:
   vaciar el buffer con límite (16 bytes; si no se vacía, no hay controlador), leer el byte de
   control hasta que dos lecturas coincidan, deshabilitar el teclado y su IRQ mientras se
   configura, **forzar la traducción a set 1** si el firmware la dejó apagada (keyboard.e sólo
   entiende set 1; se imprime `traduccion_forzada=1`), probar el puerto auxiliar (bucle `0xD3`
   con `AUXDATA`, si no `0xA9`, y el bit `AUXDIS` con `0xA7/0xA8`), habilitar el puerto del
   teclado sin IRQ, reiniciar el teclado (`0xFF → 0xFA, 0xAA`, no fatal) y por último su IRQ.
   El ratón lo configura `pointer.e` por `i8042_aux_command` (reinicio, `0xF6`, `0xF4`) y pide
   `i8042_enable_aux`. Toda espera tiene plazo. Justo antes de habilitar las interrupciones
   (`sched_start`), `i8042_drain` vacía el buffer: una entrada por flanco no ve un byte que ya
   estaba esperando, y el 8042 no acepta el siguiente hasta que se lea ése. La interrupción es
   una sola rutina (`i8042_irq`) para IRQ1 e IRQ12: lee el estado primero, reparte por el bit
   `AUXDATA`, sigue mientras haya bytes (hasta 16, por si dos flancos llegaron como uno) y
   descarta un byte con `TIMEOUT` o `PARITY`.

## Qué se midió

Resultados en los `serial.log` de las ejecuciones citadas abajo (se rellenan con los números
de cada máquina virtual; «cuentas/10ms» es la cuenta del temporizador con divisor 16).

| Máquina | ACPI | LAPIC | Temporizador | IOAPIC | MSI del AHCI |
|---|---|---|---|---|---|
| QEMU q35 (OVMF) | RSDP rev 2, XSDT con FACP, APIC, HPET, MCFG, WAET, BGRT (sumas OK); MADT: 1 CPU en la VM de prueba, IOAPIC en `0xFEC00000`, IRQ0→GSI2 y 4 redirecciones más; MCFG ECAM `0xE0000000` buses 0..255; HPET `0xFED00000` | id 0, versión `0x14`, 6 LVT, `IA32_APIC_BASE=0xFEE00900` (BSP) | 126 678 cuentas/10 ms con divisor 16 (~202 MHz de bus); periódico a 100 Hz; IRQ0 enmascarada; `[pit] N s` y el scheduler siguen por el vector 48 | id 0, versión `0x20`, 24 entradas | capacidad en `0x80`, 64 bits, control `0x80 → 0x81`; `[ahci] primera interrupcion MSI por el vector 49 (PxIS=0x1)` |
| VirtualBox (EFI) | RSDP rev 2, XSDT con FACP, APIC, SSDT, BGRT (sumas OK); MADT: 2 CPUs (los de la VM), IOAPIC id 2 en `0xFEC00000`, IRQ0→GSI2 e IRQ9→GSI9; sin MCFG ni HPET (chipset PIIX3) | id 0, versión `0x14`, 6 LVT, BSP | 728 395 cuentas/10 ms con divisor 16 (~1165 MHz de bus bajo Hyper-V/NEM); periódico a 100 Hz; la pila de red completa (DHCP, DNS, TCP, HTTP) corre con sus plazos medidos por este reloj | registro ID = 0 aunque la MADT diga 2 (rareza de VirtualBox), versión `0x20`, 24 entradas | el AHCI ICH8 de VirtualBox **no anuncia la capacidad MSI**: `[ahci] sin capacidad MSI: completaciones por sondeo`; el disco monta igual (`20261026-093438-qwen35`) |

## Teclado y ratón por el IOAPIC: qué se vio

| Máquina | FADT | i8042 | Rutas | Prueba |
|---|---|---|---|---|
| QEMU q35 (OVMF) | rev. ≥ 2, `boot_arch=0x2` (hay 8042), SCI = IRQ 9 | byte de control inicial `0x67` (traducción ya activa, IRQs de teclado y ratón activas, puerto auxiliar deshabilitado); auxiliar presente por el bucle `0xD3`; reinicio del teclado `0xFA, 0xAA`; ratón: reinicio `0xFA, 0xAA, 0x00` | IRQ1 → GSI 1 → vector 33, IRQ12 → GSI 12 → vector 44, flanco y activo alto (la MADT no redirige ninguna de las dos); `[int] irq N sin handler` no aparece | `[i8042] primer byte del teclado por interrupcion (0x35)` y todas las órdenes del M4 tecleadas por `sendkey` (`m4-test/20261026-102231-ttc1jnu1`); `[i8042] primer byte del raton por interrupcion (0x28)` y la D1 completa por clics (`workspace-test/20261026-102118-qhepyxxy`, `-102204-ecelivod`); red en verde sin ratón (`net-test/20261026-102505-fjk4o828`) |
| VirtualBox (EFI) | rev. ≥ 2, `boot_arch=0x3` (hay 8042 y dispositivos legacy), SCI = IRQ 9 | byte de control inicial `0x67`, igual que QEMU; auxiliar presente por el bucle `0xD3`; reinicio del teclado `0xFA, 0xAA`; ratón: reinicio `0xFA, 0xAA, 0x00` | IRQ1 → GSI 1 → vector 33, IRQ12 → GSI 12 → vector 44, flanco y activo alto (la MADT sólo redirige IRQ0→GSI2 e IRQ9); el IOAPIC sigue diciendo id 0 en el registro aunque la MADT diga 2 | `[i8042] primer byte del teclado por interrupcion (0x35)` y `/estado` tecleado por scancodes PS/2 (`VBoxManage controlvm keyboardputscancode`) respondido por el sistema (`vbox-erlanos/20261026-102547-qwen35`, con el volumen Erlan por AHCI montado); perfil red en verde con el 8259 apagado (`vbox-erlanos/20261026-102521-red`); el ratón sólo llega hasta `[pointer] PS/2 listo`: el script no inyecta movimientos |

## Qué enseñó Linux (y qué no se copió)

Por pedido de Erlan se estudiaron `drivers/input/serio/i8042.c`, `arch/x86/kernel/apic/io_apic.c`
y `arch/x86/kernel/acpi/boot.c` del kernel Linux. Linux es **GPL-2.0**: traducir su código a
Erlan haría del módulo una obra derivada bajo GPL, y el repo quiere seguir con su licencia
propia (regla de [DRIVERS-ESTUDIO.md §4](DRIVERS-ESTUDIO.md)). Así que se leyó para aprender la
**secuencia y las rarezas de hardware**, y se escribió desde las especificaciones. Lo aprendido:

- **i8042**: el byte de control se lee hasta que dos lecturas coincidan (controladores que
  devuelven basura al principio); el teclado se deshabilita mientras se configura; el buffer se
  vacía con un límite de 16 bytes y, si no se vacía, es que no hay controlador; el puerto
  auxiliar se prueba con el bucle `0xD3` y la prueba `0xA9` (acepta `0x00`, `0xFA`, `0xFF`) y
  comprobando que `0xA7/0xA8` mueven el bit `AUXDIS`; cada espera tiene plazo (Linux: 10000 ×
  50 µs); el puerto del teclado se **habilita antes de hablarle** (un 8042 real no manda nada
  por un puerto con `KBDDIS`; en Linux lo hace `i8042_enable_kbd_port` antes de que `atkbd`
  sondee); la rutina de interrupción lee el **estado antes del dato** y reparte por `AUXDATA`.
  Diferencia buscada: Linux respeta un firmware que dejó la traducción apagada y sabe leer
  set 2; `keyboard.e` sólo entiende set 1, así que `i8042.e` enciende la traducción y lo dice.
  Diferencia aceptada: Linux no reinicia el teclado salvo `i8042.reset=always`; aquí se
  reinicia una vez (no fatal), porque es la manera de saber que hay teclado detrás.
- **IOAPIC**: la entrada se escribe alta y luego baja (y se enmascara antes de cambiarla); IRQ
  ISA sin redirección = identidad, flanco, activo alto; las banderas de la MADT se decodifican
  bits 1:0 (polaridad) y 3:2 (disparo), con 0 = valor del bus; al dejar el modo PIC hay que
  enmascarar LINT0 del LAPIC («the virtual wire has to be disabled»); el EOI de una entrada por
  nivel va al LAPIC y, en IOAPIC versión ≥ `0x20`, también al registro EOI `0x40` del IOAPIC
  (todavía no se usa: teclado y ratón son por flanco); el IOAPIC versión `0x11` tiene una errata
  al EOI por nivel (enmascarar, pasar a flanco, restaurar); IRQ0 va a GSI2 y se fuerza a flanco;
  IRQ2 nunca se ruta.
- **ACPI**: la bandera `IAPC_BOOT_ARCH` de la FADT (offset 109, bit 1) dice si hay 8042; sin
  FADT moderna se asume que sí. Sin ella, una PC con teclado USB y sin emulación PS/2 (como
  esta, ver [DRIVERS-ESTUDIO.md §1](DRIVERS-ESTUDIO.md)) podría colgarse esperando a un 8042
  que no existe.

## Qué no hace todavía

- No ruta ninguna interrupción por nivel (SCI, PCI INTx): sólo las dos ISA por flanco. El EOI
  al IOAPIC (registro `0x40`) y la errata de la versión `0x11` quedan descritos arriba.
- No lee scancodes set 2: si el firmware apaga la traducción, `i8042.e` la vuelve a encender.
- No arranca las otras CPUs (SMP): la MADT ya dice cuántas hay y sus ids.
- No usa el ECAM de MCFG: `pci.e` sigue en el mecanismo 1 (256 bytes por función).
- No usa el HPET ni el TSC como reloj: el LAPIC a 100 Hz sustituye al PIT, nada más.
- MSI-X existe desde el NVMe (`pci_enable_msix`, [NVME.md](NVME.md)) con una sola entrada; falta
  repartir vectores por cola (xHCI, NVMe con varias colas).

## Cómo se comprobó

- QEMU: `scripts/qemu_workspace_test.py --storage ahci` (aceptación D1 por AHCI con MSI, el
  reloj en el LAPIC y el **ratón PS/2 por IRQ12 → vector 44**: `input-send-event` de QEMU),
  `scripts/qemu_m4_test.py` (modelo, capabilities, scheduler; **teclado por IRQ1 → vector 33**:
  `sendkey` de QEMU) y `make test-net` (DHCP, DNS, TCP y HTTP con los plazos medidos por el
  tick del LAPIC).
- VirtualBox: `scripts\vbox_erlanos.ps1 -Perfil red` y `-Perfil qwen35 -Taller`.
- `make test` en verde.
