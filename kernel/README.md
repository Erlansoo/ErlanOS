# L1 — Microkernel

x86-64, protocolo Limine. Sólo mecanismos deterministas: memoria, interrupciones,
scheduler, IPC, capabilities. Nada de IA aquí. Escrito en Erlan; C sólo como pegamento.

Módulos en Erlan (`.e`, compilados por erlancompiler a `build/gen/*.c,*.h`):

- `gdt.e`       GDT: segmentos kernel/usuario (TSS en M2)
- `idt.e`       IDT de 256 vectores, `isr_dispatch`: excepciones con volcado de registros, IRQs
- `pic.e`       8259: remapeo a 32..47, EOI, máscaras
- `pit.e`       timer a 100 Hz, ticks y uptime
- `keyboard.e`  PS/2 set 1 con shift
- `pmm.e`       páginas físicas: bitmap sobre el memmap de Limine, 1 MiB bajo reservado
- `heap.e`      `kalloc`/`kfree` first-fit con fusión sobre una arena de 16 MiB
- `fpu.e`       CR0/CR4/XCR0: x87, SSE, AVX, AVX-512 si existe; reporta cpuid
- `fb.e`        framebuffer lineal, pantalla `• • •`
- `vmm.e`       espacios de direcciones: PML4 por proceso, mapeo de páginas, traducción
- `sched.e`     hilos, round-robin por quantum, XSAVE/XRSTOR, sleep/block/wake, tabla de capabilities
- `ipc.e`       endpoints síncronos de un slot (256 bytes), bloqueo de emisor/receptor
- `syscall.e`   `int 0x80`: exit, log, yield, getpid, sleep, send, recv, uptime, console, getkey,
                map_module, module_size, map_anon, halo; toda syscall con efecto exige cap
- `proc.e`      carga binarios planos (módulos de Limine) en 0x400000; registra módulos de datos
                (el GGUF) y los mapea sólo lectura en 0x40000000; memoria anónima por proceso en 0x20000000
- `console.e`   consola de texto 8x16 sobre el framebuffer y el halo de estado (idle/escuchando/pensando/actuando)
- `pci.e`       inventario PCI de sólo lectura (G0), captura del NIC objetivo y la única escritura acotada: `pci_enable_bus_master`
- `e1000.e`     mecanismo NIC Intel 82540EM: anillos RX/TX, TX síncrono, RX por sondeo (N0); sin protocolo

Pegamento en C (sólo lo que exige ensamblador o el protocolo de boot):

- `main.c`        entrada `kmain`: requests Limine, orden de arranque, prueba `int3`
- `cpu.h`         inline asm: I/O de puertos, lgdt/lidt/ltr, CR*, xsave/xrstor, cpuid, cli/sti/hlt
- `switch.c/h`    `switch_context` (callee-saved + rsp) y `thread_trampoline` (iretq inicial)
- `kmem.h`        kmemcpy/kmemset por dirección para el código Erlan
- `mmio.h`        accesos MMIO volátiles y `mfence`/`pause` para drivers
- `pci_io.h`      shims CONF1: lectura (cero escrituras CFC) y la escritura acotada, ambos auditados
- `netplat.h`     plataforma para `net/*.e` dentro del kernel de prueba de red
- `font8x16.h`    fuente Consolas 8x16 rasterizada por `scripts/gen_font.ps1` (.NET, sin dependencias)
- `isr_stubs.c`   256 stubs de interrupción (generado por `scripts/gen_isr.py`)
- `limine.h`      subconjunto del protocolo Limine
- `serial.c/h`    COM1 y `kprintf`
- `panic.c/h`     `kpanic` y `erlan_panic`
- `string.c`      memcpy/memset/memmove/memcmp exigidos por clang en freestanding
- `linker.ld`      layout half superior 0xffffffff80000000, secciones de requests Limine
- `limine.conf`    entrada de boot
