/* Compile and inspect privileged code; never execute port I/O in the host. */
#include "pci_io.h"
__attribute__((noinline)) u32 pci_test_shim(u32 selector) {
    return pci_conf1_read32(selector);
}
__attribute__((noinline)) void pci_test_shim_write(u32 selector, u32 value) {
    pci_conf1_write32(selector, value);
}
