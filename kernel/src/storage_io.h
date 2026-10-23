/* x86 port primitives only; device policy and VirtIO logic live in storage.e. */
#ifndef ERLAN_STORAGE_IO_H
#define ERLAN_STORAGE_IO_H
#include "erlan_rt.h"
static inline u16 storage_inw(u16 p) { u16 v; __asm__ volatile("inw %1,%0":"=a"(v):"Nd"(p):"memory"); return v; }
static inline u32 storage_inl(u16 p) { u32 v; __asm__ volatile("inl %1,%0":"=a"(v):"Nd"(p):"memory"); return v; }
static inline void storage_outw(u16 p,u16 v) { __asm__ volatile("outw %0,%1"::"a"(v),"Nd"(p):"memory"); }
static inline void storage_outl(u16 p,u32 v) { __asm__ volatile("outl %0,%1"::"a"(v),"Nd"(p):"memory"); }
#endif
