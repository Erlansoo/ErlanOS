#ifndef ERLANOS_ISR_H
#define ERLANOS_ISR_H
#include "erlan_rt.h"
/* Dirección del stub de entrada para el vector n (0..255). Los stubs guardan los
 * registros y llaman a isr_dispatch(ptr<IntFrame>) definido en idt.e */
u64 isr_stub_addr(u64 n);
#endif
