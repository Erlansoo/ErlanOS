#include "serial.h"
#include <stdarg.h>

#define COM1 0x3F8

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port) : "memory");
}

static inline uint8_t inb(uint16_t port) {
    uint8_t ret;
    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port) : "memory");
    return ret;
}

void serial_init(void) {
    outb(COM1 + 1, 0x00);    /* sin interrupciones */
    outb(COM1 + 3, 0x80);    /* DLAB on */
    outb(COM1 + 0, 0x01);    /* divisor 1 -> 115200 baud */
    outb(COM1 + 1, 0x00);
    outb(COM1 + 3, 0x03);    /* 8N1 */
    outb(COM1 + 2, 0xC7);    /* FIFO */
    outb(COM1 + 4, 0x0B);    /* RTS/DSR */
}

static int tx_ready(void) { return inb(COM1 + 5) & 0x20; }

void serial_putc(char c) {
    if (c == '\n') serial_putc('\r');
    while (!tx_ready()) { }
    outb(COM1, (uint8_t)c);
}

void serial_puts(const char *s) {
    while (*s) serial_putc(*s++);
}

void serial_put_hex(uint64_t v) {
    static const char *d = "0123456789abcdef";
    serial_puts("0x");
    int started = 0;
    for (int i = 60; i >= 0; i -= 4) {
        int nib = (v >> i) & 0xF;
        if (nib || started || i == 0) { serial_putc(d[nib]); started = 1; }
    }
}

void serial_put_dec(uint64_t v) {
    char buf[21];
    int i = 20;
    buf[i] = 0;
    if (v == 0) { serial_putc('0'); return; }
    while (v) { buf[--i] = '0' + (v % 10); v /= 10; }
    serial_puts(&buf[i]);
}

void kprintf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { serial_putc(*p); continue; }
        p++;
        switch (*p) {
            case 's': serial_puts(va_arg(ap, const char *)); break;
            case 'x': serial_put_hex(va_arg(ap, uint64_t)); break;
            case 'd': serial_put_dec(va_arg(ap, uint64_t)); break;
            case 'c': serial_putc((char)va_arg(ap, int)); break;
            case '%': serial_putc('%'); break;
            case 0:   va_end(ap); return;
            default:  serial_putc('%'); serial_putc(*p); break;
        }
    }
    va_end(ap);
}
