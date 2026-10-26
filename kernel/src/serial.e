// L1 — puerto serie COM1 y kprintf (%s %x %d %c %%). Es la primera voz del kernel y el
// oráculo de todas las pruebas (serial.log). Sustituye a serial.c desde el 26/10/2026.
import cpu;

const COM1: u16 = 0x3F8;
let HEX: str = "0123456789abcdef";

fn serial_init() {
    outb(COM1 + 1, 0x00);    // sin interrupciones
    outb(COM1 + 3, 0x80);    // DLAB on
    outb(COM1 + 0, 0x01);    // divisor 1 -> 115200 baud
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03);    // 8N1
    outb(COM1 + 2, 0xC7);    // FIFO
    outb(COM1 + 4, 0x0B);    // RTS/DSR
}

fn tx_ready() -> bool { return (inb(COM1 + 5) & 0x20) != 0; }

fn serial_putc(c: u8) {
    if c == '\n' { serial_putc('\r'); }
    while !tx_ready() { }
    outb(COM1, c);
}

fn serial_puts(s: str) {
    var i: u64 = 0;
    unsafe { while s[i] != 0 { serial_putc(s[i]); i += 1; } }
}

fn serial_put_hex(v: u64) {
    serial_puts("0x");
    var started = false;
    var i: i64 = 60;
    while i >= 0 {
        let nib = ((v >> (i as u64)) & 0xF) as u64;
        if nib != 0 || started || i == 0 {
            unsafe { serial_putc(HEX[nib]); }
            started = true;
        }
        i -= 4;
    }
}

fn serial_put_dec(v: u64) {
    if v == 0 { serial_putc('0'); return; }
    var buf: [21]u8;
    var i: u64 = 21;
    var x = v;
    while x != 0 { i -= 1; buf[i] = '0' + ((x % 10) as u8); x /= 10; }
    while i < 21 { serial_putc(buf[i]); i += 1; }
}

// Mini formateo con argumentos variables: %s %x %d %c %%.
fn kprintf(fmt: str, ...) {
    vararg_start();
    var i: u64 = 0;
    unsafe {
        while fmt[i] != 0 {
            let c = fmt[i];
            if c != '%' { serial_putc(c); i += 1; continue; }
            i += 1;
            let f = fmt[i];
            if f == 's' { serial_puts(vararg_str()); }
            else if f == 'x' { serial_put_hex(vararg_u64()); }
            else if f == 'd' { serial_put_dec(vararg_u64()); }
            else if f == 'c' { serial_putc(vararg_i32() as u8); }
            else if f == '%' { serial_putc('%'); }
            else if f == 0 { vararg_end(); return; }
            else { serial_putc('%'); serial_putc(f); }
            i += 1;
        }
    }
    vararg_end();
}
