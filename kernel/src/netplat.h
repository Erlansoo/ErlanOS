/* Plataforma de red para los módulos net/ *.e cuando se enlazan DENTRO del kernel
 * (imagen de prueba de red). En su forma final los mismos módulos correrán en un
 * servicio Ring 3 con un netplat.h sobre syscalls SYS_NET_TX/RX. */
#ifndef ERLANOS_NETPLAT_H
#define ERLANOS_NETPLAT_H
#include "erlan_rt.h"
#include "e1000.h"
#include "pit.h"
#include "serial.h"
#include "console.h"
#include "cpu.h"

static inline bool netplat_tx(u8 *p, u64 n) { return e1000_tx((erlan_su8){ p, n }); }
static inline u64 netplat_rx(u8 *p, u64 max) { return e1000_rx((erlan_su8){ p, max }); }
static inline u64 netplat_now_ms(void) { return pit_uptime_ms(); }
/* Espera pasiva: hlt despierta con cada tick del PIT (10 ms). Requiere IF=1. */
static inline void netplat_wait_ms(u64 ms) {
    u64 end = pit_uptime_ms() + ms;
    while (pit_uptime_ms() < end) cpu_hlt();
}
static inline void netplat_log(const char *s) { kprintf("[net] %s\n", s); }
static inline void netplat_console(u8 *p, u64 n) { console_write(p, n); }
static inline void netplat_mac(u8 *out) { e1000_mac((erlan_su8){ out, 6 }); }

#endif
