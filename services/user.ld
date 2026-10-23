/* Servicios de usuario de ErlanOS: binario plano cargado en 0x400000 (ver proc.e). */
OUTPUT_FORMAT(elf64-x86-64)
ENTRY(_start)

SECTIONS
{
    . = 0x400000;
    .text : { KEEP(*(.text._start)) *(.text .text.*) }
    . = ALIGN(16);
    .rodata : { *(.rodata .rodata.*) }
    . = ALIGN(16);
    .data : { *(.data .data.*) }
    . = ALIGN(16);
    .bss : { *(.bss .bss.*) *(COMMON) }
    /* A loadable byte after NOBITS makes llvm-objcopy include the whole BSS
       gap as zeros. proc_spawn maps the resulting exact flat image length. */
    .image_end : { BYTE(0); }
    ASSERT(. <= 0xFF0000, "user image overlaps the 64 KiB user stack")
    /DISCARD/ : { *(.eh_frame*) *(.note .note.*) *(.comment) }
}
