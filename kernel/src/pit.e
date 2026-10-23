// L1 — temporizador 8253/8254 (PIT). Base de tiempo del scheduler en M2.

extern c "cpu.h" { fn outb(port: u16, v: u8); }
extern c "serial.h" { fn kprintf(fmt: str, ...); }

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
