// L1 — Global Descriptor Table y TSS. En modo largo los segmentos son planos; la GDT
// define privilegios (Ring 0 / Ring 3) y el TSS da la pila de kernel al entrar desde Ring 3.

import cpu;
import serial;

const GDT_KCODE: u16 = 0x08;
const GDT_KDATA: u16 = 0x10;
const GDT_UDATA: u16 = 0x18;   // selector de usuario: 0x1B (RPL 3)
const GDT_UCODE: u16 = 0x20;   // selector de usuario: 0x23 (RPL 3)
const GDT_TSS: u16 = 0x28;

// null, kcode, kdata, udata, ucode, tss(lo), tss(hi)
var gdt: [7]u64;

// TSS de 104 bytes visto como 26 u32 (evita padding): rsp0 en bytes 4..11.
var tss: [26]u32;

fn gdt_init() {
    gdt[0] = 0;
    gdt[1] = 0x00AF9A000000FFFF;   // código 64-bit, DPL0
    gdt[2] = 0x00CF92000000FFFF;   // datos, DPL0
    gdt[3] = 0x00CFF2000000FFFF;   // datos, DPL3
    gdt[4] = 0x00AFFA000000FFFF;   // código 64-bit, DPL3

    for i in 0..26 { tss[i] = 0; }
    tss[25] = 104 << 16;           // I/O map base = tamaño del TSS (sin mapa)
    var base: u64 = 0;
    unsafe { base = &tss[0] as u64; }
    let limit: u64 = 103;
    gdt[5] = (limit & 0xFFFF)
           | ((base & 0xFFFF) << 16)
           | (((base >> 16) & 0xFF) << 32)
           | (0x89 << 40)                      // TSS 64-bit disponible, presente, DPL0
           | (((limit >> 16) & 0xF) << 48)
           | (((base >> 24) & 0xFF) << 56);
    gdt[6] = base >> 32;

    unsafe { cpu_lgdt(&gdt[0] as u64, 7 * 8 - 1); }
    cpu_reload_segments(GDT_KCODE, GDT_KDATA);
    cpu_ltr(GDT_TSS);
    kprintf("[gdt] kcode=%x kdata=%x ucode=%x udata=%x tss=%x\n",
            GDT_KCODE as u64, GDT_KDATA as u64, GDT_UCODE as u64, GDT_UDATA as u64, GDT_TSS as u64);
}

// Pila de kernel que usará el CPU al entrar desde Ring 3 (interrupción o int 0x80).
fn tss_set_rsp0(rsp: u64) {
    tss[1] = (rsp & 0xFFFFFFFF) as u32;
    tss[2] = (rsp >> 32) as u32;
}

fn gdt_user_code() -> u64 { return (GDT_UCODE as u64) | 3; }
fn gdt_user_data() -> u64 { return (GDT_UDATA as u64) | 3; }
