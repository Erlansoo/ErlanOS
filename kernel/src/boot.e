// L1 — arranque común a las tres imágenes del kernel (normal, diagnóstico G0 y prueba de
// red): las peticiones del protocolo Limine, la lectura del mapa de memoria y la consola.
// Las entradas kmain viven en main.e (normal) y kernel/entry/*.e (sondas).
// Sustituye la parte común de main.c desde el 26/10/2026.
import limine;
import serial;
import panic;
import fb;
import console;
import pmm;

// Valores del protocolo que este módulo necesita (las constantes de limine.e son privadas
// de ese módulo, como en todo Erlan v0: cada módulo declara las suyas).
const LIMINE_MAGIC0: u64 = 0xc7b1dd30df4c8b88;
const LIMINE_MAGIC1: u64 = 0x0a82e883a194f07b;
const LIMINE_MEMMAP_USABLE: u64 = 0;
const LIMINE_FRAMEBUFFER_RGB: u8 = 1;

// Las peticiones viven en sus secciones y son `volatile`: el cargador escribe `response`
// antes de que corra el kernel, y el compilador no debe suponer que siguen en cero.
@section(".limine_requests_start") @used @volatile var requests_start: [4]u64 =
    [0xf6b8f4b39de7d1ae, 0xfab91a6940fcb9cf, 0x785c6ed015d3e316, 0x181e920a7852b9d9];
@section(".limine_requests") @used @volatile var base_revision: [3]u64 =
    [0xf9562b2d5c95a6c8, 0x6a7b384944536bdc, 3];
@section(".limine_requests") @used @volatile var info_req: LimineBootloaderInfoRequest =
    LimineBootloaderInfoRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0xf55038d8e2a1202f, 0x279426fcf5f59740], revision: 0, response: 0 };
@section(".limine_requests") @used @volatile var fb_req: LimineFramebufferRequest =
    LimineFramebufferRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0x9d5827dcd881dd75, 0xa3148604f6fab11b], revision: 0, response: 0 };
@section(".limine_requests") @used @volatile var hhdm_req: LimineHhdmRequest =
    LimineHhdmRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0x48dcf1cb8ad2b852, 0x63984e959a98244b], revision: 0, response: 0 };
@section(".limine_requests") @used @volatile var memmap_req: LimineMemmapRequest =
    LimineMemmapRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0x67cf3d9d378a806f, 0xe304acdfc50c3c62], revision: 0, response: 0 };
@section(".limine_requests") @used @volatile var module_req: LimineModuleRequest =
    LimineModuleRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0x3e7e279702be32af, 0xca1c4f3bd1280cee], revision: 0, response: 0,
                          internal_module_count: 0, internal_modules: 0 };
@section(".limine_requests") @used @volatile var rsdp_req: LimineRsdpRequest =
    LimineRsdpRequest { id: [LIMINE_MAGIC0, LIMINE_MAGIC1, 0xc5e77b6b397e7b43, 0x27637845accdcf3c], revision: 0, response: 0 };
@section(".limine_requests_end") @used @volatile var requests_end: [2]u64 =
    [0xadc0e0531bb10d03, 0x9572709f31764c62];

const MAX_REGIONS: u64 = 128;
var regions: [128]MemRegion;
var region_count: u64 = 0;
var acpi_region_count: u64 = 0;
var acpi_region_base: [16]u64;
var acpi_region_len: [16]u64;
var hhdm_offset: u64 = 0;
var console_fb: Fb;

// Primeras líneas por serial: banner, cargador, HHDM y mapa de memoria.
fn boot_begin(banner: str) {
    serial_init();
    kprintf("\n%s\n", banner);
    if base_revision[2] != 0 { kpanic("limine: base revision no soportada"); }
    if info_req.response != 0 {
        unsafe {
            let info = info_req.response as ptr<LimineBootloaderInfoResponse>;
            kprintf("bootloader: %s %s\n", info.name as str, info.version as str);
        }
    }
    if hhdm_req.response == 0 || memmap_req.response == 0 { kpanic("limine: sin hhdm o memmap"); }
    unsafe {
        let hh = hhdm_req.response as ptr<LimineHhdmResponse>;
        hhdm_offset = hh.offset;
        kprintf("hhdm offset: %x\n", hhdm_offset);
        let mm = memmap_req.response as ptr<LimineMemmapResponse>;
        let entries = mm.entries as ptr<u64>;    // tabla de punteros a LimineMemmapEntry
        region_count = 0;
        for i in 0..mm.entry_count {
            if region_count >= MAX_REGIONS { break; }
            let e = entries[i] as ptr<LimineMemmapEntry>;
            kprintf("  %x  +%x  %s\n", e.base, e.length, limine_memmap_type_name(e.kind));
            regions[region_count].base = e.base;
            regions[region_count].len = e.length;
            regions[region_count].usable = e.kind == LIMINE_MEMMAP_USABLE;
            region_count += 1;
            if (e.kind == 2 || e.kind == 3) && acpi_region_count < 16 {
                acpi_region_base[acpi_region_count] = e.base; acpi_region_len[acpi_region_count] = e.length;
                acpi_region_count += 1;
            }
        }
    }
}

fn boot_hhdm() -> u64 { return hhdm_offset; }
// Regiones acpi-reclaim y acpi-nvs del mapa: el HHDM de Limine no las mapea; acpi.e las mapea.
fn boot_acpi_region_count() -> u64 { return acpi_region_count; }
fn boot_acpi_region_base(i: u64) -> u64 { if i < acpi_region_count { return acpi_region_base[i]; } return 0; }
fn boot_acpi_region_len(i: u64) -> u64 { if i < acpi_region_count { return acpi_region_len[i]; } return 0; }

// Dirección virtual del RSDP de ACPI (0 si el cargador no lo entregó). Limine lo da en
// físico desde la revisión base 3; una dirección ya en la mitad superior se respeta.
fn boot_rsdp() -> u64 {
    if rsdp_req.response == 0 { return 0; }
    unsafe {
        let r = rsdp_req.response as ptr<LimineRsdpResponse>;
        if r.address == 0 { return 0; }
        if r.address >= hhdm_offset { return r.address; }
        return r.address + hhdm_offset;
    }
}
fn boot_regions() -> []MemRegion { return regions[0..region_count]; }

// Pantalla: el framebuffer que entrega el firmware (GOP de UEFI) a través de Limine.
fn boot_console() {
    if fb_req.response == 0 { kpanic("sin framebuffer"); }
    unsafe {
        let r = fb_req.response as ptr<LimineFramebufferResponse>;
        if r.framebuffer_count < 1 { kpanic("sin framebuffer"); }
        let fbs = r.framebuffers as ptr<u64>;
        let lf = fbs[0] as ptr<LimineFramebuffer>;
        // El renderizador escribe píxeles u32: se rechaza cualquier otro modo del firmware.
        if lf.address == 0 || lf.bpp != 32 || lf.memory_model != LIMINE_FRAMEBUFFER_RGB ||
           lf.width == 0 || lf.height == 0 || lf.width > 0x3fffffffffffffff ||
           lf.pitch < lf.width * 4 || lf.red_mask_shift > 24 ||
           lf.green_mask_shift > 24 || lf.blue_mask_shift > 24 {
            kpanic("framebuffer: modo RGB32 requerido");
        }
        kprintf("framebuffer: %dx%d bpp=%d pitch=%d\n", lf.width, lf.height, lf.bpp as u64, lf.pitch);
        fb_init(&mut console_fb, lf.address as ptr<u32>, lf.width, lf.height, lf.pitch,
                lf.red_mask_shift, lf.green_mask_shift, lf.blue_mask_shift);
    }
    console_init(&mut console_fb);
}

// Módulos de Limine (archivos extra cargados por el bootloader).
fn boot_module_count() -> u64 {
    if module_req.response == 0 { return 0; }
    unsafe { let mr = module_req.response as ptr<LimineModuleResponse>; return mr.module_count; }
}
fn boot_module(i: u64) -> ptr<LimineFile> {
    unsafe {
        let mr = module_req.response as ptr<LimineModuleResponse>;
        let mods = mr.modules as ptr<u64>;
        return mods[i] as ptr<LimineFile>;
    }
}
