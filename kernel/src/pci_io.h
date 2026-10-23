/* G0 x86 PCI configuration mechanism 1.
 * pci_conf1_read32: only the address selector is written (audited: zero CFC writes).
 * pci_conf1_write32: the ONE bounded data write the kernel performs, used solely by
 * pci_enable_bus_master() on the NIC the kernel itself identified (Memory Space +
 * Bus Master bits of the Command register). It is never reachable from Ring 3 and
 * is audited separately (exactly one CFC write, selector restored, IF preserved).
 * The current kernel runs one CPU. Before SMP, protect this shared port pair
 * with a global lock in addition to disabling local interrupts. */
#ifndef ERLANOS_PCI_IO_H
#define ERLANOS_PCI_IO_H
#include "erlan_rt.h"

static inline u32 pci_conf1_read32(u32 selector) {
    u64 flags;
    u32 previous, value;
    __asm__ volatile ("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
    __asm__ volatile ("inl %1, %0" : "=a"(previous) : "Nd"((u16)0xCF8) : "memory");
    __asm__ volatile ("outl %0, %1" : : "a"(selector), "Nd"((u16)0xCF8) : "memory");
    __asm__ volatile ("inl %1, %0" : "=a"(value) : "Nd"((u16)0xCFC) : "memory");
    __asm__ volatile ("outl %0, %1" : : "a"(previous), "Nd"((u16)0xCF8) : "memory");
    /* IF=0 on entry must remain disabled, including early boot and IRQ callers. */
    if (flags & (1ULL << 9)) __asm__ volatile ("sti" : : : "memory");
    return value;
}

static inline void pci_conf1_write32(u32 selector, u32 value) {
    u64 flags;
    u32 previous;
    __asm__ volatile ("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
    __asm__ volatile ("inl %1, %0" : "=a"(previous) : "Nd"((u16)0xCF8) : "memory");
    __asm__ volatile ("outl %0, %1" : : "a"(selector), "Nd"((u16)0xCF8) : "memory");
    __asm__ volatile ("outl %0, %1" : : "a"(value), "Nd"((u16)0xCFC) : "memory");
    __asm__ volatile ("outl %0, %1" : : "a"(previous), "Nd"((u16)0xCF8) : "memory");
    if (flags & (1ULL << 9)) __asm__ volatile ("sti" : : : "memory");
}
#endif
