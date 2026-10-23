#ifndef M4_HOST_CPU_H
#define M4_HOST_CPU_H
#include "erlan_rt.h"
/* Host-only replacement of the privileged page-table register instructions. */
u64 cpu_read_cr3(void);
void cpu_write_cr3(u64 n);
void cpu_invlpg(u64 n);
u8 inb(u16 port);
#endif
