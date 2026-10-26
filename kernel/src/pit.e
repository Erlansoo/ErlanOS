// L1 — temporizador 8253/8254 (PIT). Base de tiempo del scheduler en M2; desde que el reloj
// del sistema vive en el LAPIC (apic.e), el canal 2 sirve además de espera medida en
// milisegundos para los plazos de inicialización de los drivers (NVMe: CSTS.RDY).

import cpu;
import mmio;
import serial;

const PIT_HZ_BASE: u32 = 1193182;

var ticks: u64 = 0;
var hz: u32 = 0;

fn pit_init(freq: u32) {
    hz = freq;
    let div = PIT_HZ_BASE / freq;
    outb(0x43, 0x36);                       // canal 0, lo/hi, modo 3 (onda cuadrada)
    outb(0x40, (div & 0xFF) as u8);
    outb(0x40, ((div >> 8) & 0xFF) as u8);
    kprintf("[pit] %d Hz (divisor %d)\n", freq as u64, div as u64);
}

// IRQ0
fn pit_tick() {
    ticks += 1;
    if ticks % (hz as u64) == 0 {
        kprintf("[pit] %d s\n", ticks / (hz as u64));
    }
}

fn pit_ticks() -> u64 { return ticks; }

fn pit_uptime_ms() -> u64 {
    if hz == 0 { return 0; }
    return ticks * 1000 / (hz as u64);
}

// Espera `ms` milisegundos con el canal 2 en modo 0 (un disparo), sin interrupciones: la
// puerta se abre por el bit 0 del puerto 0x61 y el fin de cuenta se lee en su bit 5. Tramos
// de hasta 50 ms porque el contador es de 16 bits (1193182 / 20 = 59659 < 65536).
fn pit_sleep_ms(ms: u64) {
    var left = ms;
    while left > 0 {
        var chunk = left;
        if chunk > 50 { chunk = 50; }
        let count = (PIT_HZ_BASE as u64) * chunk / 1000;
        let gate = inb(0x61);
        outb(0x61, (gate & 0xFD) | 0x01);           // altavoz apagado, puerta del canal 2 abierta
        outb(0x43, 0xB0);                            // canal 2, lo/hi, modo 0
        outb(0x42, (count & 0xFF) as u8);
        outb(0x42, ((count >> 8) & 0xFF) as u8);
        var spins: u64 = 0;
        while (inb(0x61) & 0x20) == 0 && spins < 50000000 { cpu_pause(); spins += 1; }
        outb(0x61, gate);
        left -= chunk;
    }
}
