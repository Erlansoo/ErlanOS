#ifndef ERLANOS_SWITCH_H
#define ERLANOS_SWITCH_H
#include "erlan_rt.h"
/* Guarda los registros callee-saved en la pila actual, almacena rsp en *old_rsp,
 * carga new_rsp, restaura registros y retorna en el nuevo contexto. */
void switch_context(u64 *old_rsp, u64 new_rsp);
/* Dirección de `thread_trampoline`: un `iretq` que arranca un hilo nuevo con el
 * frame preparado por sched.e */
u64 thread_trampoline_addr(void);
#endif
