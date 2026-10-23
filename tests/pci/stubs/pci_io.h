#ifndef ERLANOS_TEST_PCI_IO_H
#define ERLANOS_TEST_PCI_IO_H
#include "erlan_rt.h"
u32 pci_conf1_read32(u32 selector);
void pci_conf1_write32(u32 selector, u32 value);
#endif
