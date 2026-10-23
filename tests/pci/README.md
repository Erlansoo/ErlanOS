# PCI G0: inventario de lectura

Ejecutar `python scripts/test_pci_host.py` desde la raíz del proyecto. Compila
`kernel/src/pci.e` con el compilador real de Erlan y enlaza un espacio de
configuración sintético. No accede al PCI de Windows/Linux.

La prueba cubre los límites completos de CONF1, función 0 ausente, dispositivos
multifunción con huecos, puentes, bus 255, identidad y campos de diagnóstico,
BAR de E/S, memoria de 32/64 bits y legado de 20 bits. También comprueba los
tipos reservados, BAR de 64 bits truncados por el límite de la cabecera,
direcciones sin asignar, reinicialización y las 65.536 funciones posibles sin
truncar el inventario. El checksum del espacio simulado se mantiene idéntico.

El shim nativo se compila aparte para x86-64 freestanding. `shim_audit.py`
interpreta el ensamblador emitido por clang con IF inicialmente a cero y a uno:
comprueba la restauración de IF y del selector CF8, y que no exista ninguna
escritura a CFC. Rechaza instrucciones que no conozca. Es una auditoría del
código generado, no una ejecución de instrucciones privilegiadas en el host.

La validación con dispositivos emulados de QEMU está en
`scripts/qemu_pci_test.py`; el inventario de una GPU física requiere arrancar la
imagen de diagnóstico en el equipo real. Ninguna prueba sintética demuestra
cómputo GPU, red ni soporte físico de NVIDIA.

## Interfaz del kernel

- `pci_init()` recorre el segmento 0, 256 buses y 32 ranuras; comprueba las
  funciones 1 a 7 únicamente si la función 0 anuncia multifunción.
- `pci_count()`, `pci_display_count()` y `pci_network_count()` exponen el último
  inventario. `pci_target_gpu_count()` cuenta `10de:1c82` y
  `pci_target_nic_count()` cuenta `8086:15b8`.
- `pci_invalid_bar_count()` cuenta descriptores BAR reservados o malformados.
- `pci_read_device(bus, slot, function)` obtiene un `PciDevice` por BDF sin
  depender de un array de capacidad fija. Se debe comprobar `present`.
- `pci_read32(bus, slot, function, offset)` sólo admite offsets alineados de
  0 a 252. Rechaza argumentos fuera de rango antes de reducirlos a 32 bits.

`PciBar` conserva valor bruto, base de 64 bits, tipo, prefetch y si hay dirección
asignada. No conoce el tamaño del recurso: averiguarlo escribiendo unos en el BAR
queda expresamente fuera de G0. Los slots superiores de un BAR de 64 bits no se
interpretan como un segundo BAR. CardBus y cabeceras desconocidas se identifican
sin aplicarles el formato de BAR de una cabecera normal.

Este mecanismo no asigna buses ni recursos y, por tanto, depende de la
configuración establecida por el firmware. No examina segmentos superiores a
cero, ECAM, capacidades PCIe extendidas ni ARI. Al introducir SMP habrá que añadir
un lock global: deshabilitar IRQ locales basta únicamente con el kernel actual
de un procesador.

Las codificaciones se contrastaron con las referencias primarias de Linux:
[registros PCI](https://github.com/torvalds/linux/blob/master/include/uapi/linux/pci_regs.h)
y [acceso CONF1](https://github.com/torvalds/linux/blob/master/arch/x86/pci/direct.c).
La implementación en Erlan es propia; no contiene código del controlador Linux.
