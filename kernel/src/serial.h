#ifndef ERLANOS_SERIAL_H
#define ERLANOS_SERIAL_H

#include <stdint.h>

void serial_init(void);
void serial_putc(char c);
void serial_puts(const char *s);
void serial_put_hex(uint64_t v);
void serial_put_dec(uint64_t v);

/* Mini formateo: %s %x %d %c %% */
void kprintf(const char *fmt, ...);

#endif
