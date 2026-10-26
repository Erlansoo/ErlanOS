// Subconjunto del protocolo de arranque Limine usado por ErlanOS, en Erlan.
// Referencia: https://github.com/limine-bootloader/limine/blob/trunk/PROTOCOL.md
// Sólo las peticiones que L1 necesita: revisión base, información del cargador,
// framebuffer, HHDM, mapa de memoria y módulos (el GGUF se carga como módulo).
// Los punteros del protocolo se guardan como u64 (misma ABI: 8 bytes) y se convierten
// en `unsafe` al leerlos: el cargador los rellena antes de que corra el kernel y
// pueden ser nulos. Sustituye a limine.h (C) desde el 26/10/2026.

const LIMINE_MAGIC0: u64 = 0xc7b1dd30df4c8b88;
const LIMINE_MAGIC1: u64 = 0x0a82e883a194f07b;

// ---- Archivo (módulos cargados por el bootloader) ----
struct LimineUuid { a: u32; b: u16; c: u16; d: [8]u8; }

struct LimineFile {
    revision: u64;
    address: u64;        // ptr<u8>
    size: u64;
    path: u64;           // str
    string: u64;         // str
    media_type: u32;
    unused: u32;
    tftp_ip: u32;
    tftp_port: u32;
    partition_index: u32;
    mbr_disk_id: u32;
    gpt_disk_uuid: LimineUuid;
    gpt_part_uuid: LimineUuid;
    part_uuid: LimineUuid;
}

// ---- Bootloader info ----
struct LimineBootloaderInfoResponse { revision: u64; name: u64; version: u64; }
struct LimineBootloaderInfoRequest { id: [4]u64; revision: u64; response: u64; }

// ---- Framebuffer ----
const LIMINE_FRAMEBUFFER_RGB: u8 = 1;

struct LimineFramebuffer {
    address: u64;
    width: u64;
    height: u64;
    pitch: u64;
    bpp: u16;
    memory_model: u8;
    red_mask_size: u8;
    red_mask_shift: u8;
    green_mask_size: u8;
    green_mask_shift: u8;
    blue_mask_size: u8;
    blue_mask_shift: u8;
    unused: [7]u8;
    edid_size: u64;
    edid: u64;
    mode_count: u64;     // revisión 1
    modes: u64;
}
struct LimineFramebufferResponse { revision: u64; framebuffer_count: u64; framebuffers: u64; }
struct LimineFramebufferRequest { id: [4]u64; revision: u64; response: u64; }

// ---- HHDM (mapa directo en la mitad superior) ----
struct LimineHhdmResponse { revision: u64; offset: u64; }
struct LimineHhdmRequest { id: [4]u64; revision: u64; response: u64; }

// ---- Mapa de memoria ----
const LIMINE_MEMMAP_USABLE: u64 = 0;
const LIMINE_MEMMAP_RESERVED: u64 = 1;
const LIMINE_MEMMAP_ACPI_RECLAIMABLE: u64 = 2;
const LIMINE_MEMMAP_ACPI_NVS: u64 = 3;
const LIMINE_MEMMAP_BAD_MEMORY: u64 = 4;
const LIMINE_MEMMAP_BOOTLOADER_RECLAIMABLE: u64 = 5;
const LIMINE_MEMMAP_EXECUTABLE_AND_MODULES: u64 = 6;
const LIMINE_MEMMAP_FRAMEBUFFER: u64 = 7;

struct LimineMemmapEntry { base: u64; length: u64; kind: u64; }
struct LimineMemmapResponse { revision: u64; entry_count: u64; entries: u64; }
struct LimineMemmapRequest { id: [4]u64; revision: u64; response: u64; }

// ---- RSDP (ACPI) ----
// Con revisión base 3 del protocolo la dirección es física; antes era virtual (HHDM).
struct LimineRsdpResponse { revision: u64; address: u64; }
struct LimineRsdpRequest { id: [4]u64; revision: u64; response: u64; }

// ---- Módulos ----
struct LimineModuleResponse { revision: u64; module_count: u64; modules: u64; }
struct LimineModuleRequest {
    id: [4]u64;
    revision: u64;
    response: u64;
    internal_module_count: u64;   // revisión 1: no se usa
    internal_modules: u64;
}

// Nombre legible de cada tipo de región del mapa de memoria.
fn limine_memmap_type_name(t: u64) -> str {
    if t == LIMINE_MEMMAP_USABLE { return "usable"; }
    if t == LIMINE_MEMMAP_RESERVED { return "reserved"; }
    if t == LIMINE_MEMMAP_ACPI_RECLAIMABLE { return "acpi-reclaim"; }
    if t == LIMINE_MEMMAP_ACPI_NVS { return "acpi-nvs"; }
    if t == LIMINE_MEMMAP_BAD_MEMORY { return "bad"; }
    if t == LIMINE_MEMMAP_BOOTLOADER_RECLAIMABLE { return "bootloader-reclaim"; }
    if t == LIMINE_MEMMAP_EXECUTABLE_AND_MODULES { return "kernel+modules"; }
    if t == LIMINE_MEMMAP_FRAMEBUFFER { return "framebuffer"; }
    return "?";
}
