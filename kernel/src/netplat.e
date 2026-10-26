// Plataforma de red para los módulos net/*.e cuando se enlazan DENTRO del kernel (imagen de
// prueba de red). En su forma final los mismos módulos correrán en un servicio Ring 3 con un
// netplat.e sobre syscalls SYS_NET_TX/RX. Sustituye a netplat.h (C) desde el 26/10/2026.
import e1000;
import pit;
import serial;
import console;
import cpu;

fn netplat_tx(p: ptr<u8>, n: u64) -> bool {
    var s: []u8;
    unsafe { s = p[0..n]; }
    return e1000_tx(s);
}

fn netplat_rx(p: ptr<u8>, max: u64) -> u64 {
    var s: []u8;
    unsafe { s = p[0..max]; }
    return e1000_rx(s);
}

fn netplat_now_ms() -> u64 { return pit_uptime_ms(); }

// Espera pasiva: hlt despierta con cada tick del PIT (10 ms). Requiere IF=1.
fn netplat_wait_ms(ms: u64) {
    let end = pit_uptime_ms() + ms;
    while pit_uptime_ms() < end { cpu_hlt(); }
}

fn netplat_log(s: str) { kprintf("[net] %s\n", s); }
fn netplat_console(p: ptr<u8>, n: u64) { console_write(p, n); }

fn netplat_mac(out: ptr<u8>) {
    var s: []u8;
    unsafe { s = out[0..6]; }
    e1000_mac(s);
}

// STATUS.LU del e1000: el enlace negociado. VirtualBox y el cobre real tardan segundos.
fn netplat_link_up() -> bool { return e1000_link_up(); }
