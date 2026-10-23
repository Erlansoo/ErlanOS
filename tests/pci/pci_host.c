/* Real Erlan PCI inventory with a synthetic configuration space. The fixture
 * models read transactions only: no real host PCI registers are accessed. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pci.h"

static u32 config[65536][64];
static u32 reads[65536][64];
static u32 writes[65536][64];
static u64 write_transactions;
static u64 transactions, device_lines, bar_lines, bridge_lines, end_lines;
static u64 final_devices, final_gpu, final_nic, final_invalid;

void erlan_panic(const char *message) { fprintf(stderr, "%s\n", message); abort(); }

u32 pci_conf1_read32(u32 selector) {
    assert((selector & 0x80000000U) != 0);
    assert((selector & 0x7F000003U) == 0);
    unsigned bdf = (selector >> 8) & 0xFFFF;
    unsigned word = (selector & 0xFC) >> 2;
    ++reads[bdf][word];
    ++transactions;
    return config[bdf][word];
}

/* Escritura sintetica: aplica el valor y la contabiliza para verificar que el
 * kernel solo escribe el registro Command del NIC que el mismo identifico. */
void pci_conf1_write32(u32 selector, u32 value) {
    assert((selector & 0x80000000U) != 0);
    assert((selector & 0x7F000003U) == 0);
    unsigned bdf = (selector >> 8) & 0xFFFF;
    unsigned word = (selector & 0xFC) >> 2;
    ++writes[bdf][word];
    ++write_transactions;
    config[bdf][word] = value;
}

void kprintf(const char *format, ...) {
    if (strstr(format, "[pci] dev ")) ++device_lines;
    if (strstr(format, "[pci] bar ")) ++bar_lines;
    if (strstr(format, "[pci] bridge ")) ++bridge_lines;
    if (strstr(format, "[pci] end ")) {
        va_list args;
        va_start(args, format);
        final_devices = va_arg(args, u64);
        final_gpu = va_arg(args, u64);
        final_nic = va_arg(args, u64);
        final_invalid = va_arg(args, u64);
        va_end(args);
        ++end_lines;
    }
}

static unsigned bdf(unsigned bus, unsigned slot, unsigned function) {
    return (bus << 8) | (slot << 3) | function;
}

static u32 *device(unsigned bus, unsigned slot, unsigned function, u16 vendor,
                   u16 id, u8 header, u8 class_code) {
    u32 *p = config[bdf(bus, slot, function)];
    memset(p, 0, 64 * sizeof(*p));
    p[0] = ((u32)id << 16) | vendor;
    p[2] = (u32)class_code << 24;
    p[3] = (u32)header << 16;
    return p;
}

static void reset(void) {
    memset(config, 0xFF, sizeof(config));
    memset(reads, 0, sizeof(reads));
    memset(writes, 0, sizeof(writes));
    write_transactions = 0;
    transactions = device_lines = bar_lines = bridge_lines = end_lines = 0;
}

static u64 checksum(void) {
    const unsigned char *bytes = (const unsigned char *)config;
    u64 hash = 14695981039346656037ULL;
    for (size_t i = 0; i < sizeof(config); ++i) hash = (hash ^ bytes[i]) * 1099511628211ULL;
    return hash;
}

static void test_bounds(void) {
    reset();
    const u64 invalid[][4] = {
        {256,0,0,0}, {0,32,0,0}, {0,0,8,0}, {0,0,0,256},
        {0,0,0,1}, {0,0,0,255}, {UINT64_MAX,0,0,0},
        {0,UINT64_MAX,0,0}, {0,0,UINT64_MAX,0}, {0,0,0,UINT64_MAX}
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        assert(pci_read32(invalid[i][0], invalid[i][1], invalid[i][2], invalid[i][3]) == UINT32_MAX);
    assert(transactions == 0);
    config[65535][63] = 0x12345678;
    assert(pci_read32(255,31,7,252) == 0x12345678);
    assert(reads[65535][63] == 1);
    assert(!pci_read_device(256,0,0).present);
    assert(transactions == 1);
    puts("PASS PCI argument bounds, no narrowing wrap, bus255/function7/offset252");
}

static void test_scan(void) {
    reset();
    u32 *p = device(0,0,0,0x8086,0x1237,0,6);
    p[2] |= 0x00010203; // subclass 1, prog-if 2, revision 3
    p[1] = 0xABCD0407; p[15] = 0x010B;
    // Firmware-assigned bridge numbers are observed, never programmed/trusted
    // as a recursive scan bound. Device on bus255 must still be discovered.
    p = device(0,1,0,0x1B36,0x0001,0x81,6);
    p[6] = 0x00090900;
    p[4] = 0xC0000000; p[5] = 0x00002001;
    device(0,1,7,0x1234,0x7777,0,4); // holes 1..6 must not terminate scan
    p = device(9,3,0,0x10DE,0x1C82,0x80,3);
    p[4] = 0xD0000000;             // mem32
    p[5] = 0x0000000C; p[6] = 4; // mem64 prefetch at 16GiB, low address zero
    p[7] = 0x00003001;            // IO
    p[8] = 0; p[9] = 0x00090002;// valid legacy memory below1MiB
    device(9,3,1,0x10DE,0x0FB9,0,4);
    device(255,31,0,0x8086,0x15B8,0,2);
    // Function1 behind absent function0 and non-multifunction function0 are
    // deliberately unreachable according to the conventional enumeration rule.
    device(1,2,1,0x1234,0xDEAD,0,3);
    device(2,2,0,0x1234,0xCAFE,0,1);
    device(2,2,1,0x1234,0xBEEF,0,3);
    // Absent vendor must be detected even if high device bits are not FFFF.
    config[bdf(3,0,0)][0] = 0x1234FFFF;
    p = device(4,0,0,0x1234,0x4444,2,6); // CardBus not a type0 BAR layout
    p[4] = 0x12345678; p[5] = 0x87654321;
    p = device(5,0,0,0x1234,0x5555,0x7F,0); // unknown header
    p[4] = 0xABCDEF00;
    u64 before = checksum();
    pci_init();
    assert(checksum() == before);
    assert(pci_count() == 9 && device_lines == 9 && end_lines == 1);
    assert(final_devices == 9 && final_gpu == 1 && final_nic == 1 && final_invalid == 0);
    assert(pci_target_gpu_count() == 1 && pci_target_nic_count() == 1);
    assert(pci_display_count() == 1 && pci_network_count() == 1);
    assert(bridge_lines == 1 && bar_lines == 6);
    assert(reads[bdf(1,2,1)][0] == 0 && reads[bdf(2,2,1)][0] == 0);
    for (unsigned fn = 1; fn < 8; ++fn) assert(reads[bdf(0,1,fn)][0] == 1);
    assert(reads[bdf(3,0,0)][1] == 0);
    assert(reads[bdf(0,1,0)][7] == 0); // bridge windows are not extra BARs
    assert(reads[bdf(4,0,0)][4] == 0 && reads[bdf(5,0,0)][4] == 0);
    struct PciDevice d = pci_read_device(0,0,0);
    assert(d.present && d.vendor == 0x8086 && d.device == 0x1237);
    assert(d.class_code == 6 && d.subclass == 1 && d.prog_if == 2 && d.revision == 3);
    assert(d.command == 0x0407 && d.status == 0xABCD && d.irq_line == 11 && d.irq_pin == 1);
    d = pci_read_device(9,3,0);
    assert(d.bars[0].kind == 2 && d.bars[0].base == 0xD0000000 && !d.bars[0].prefetch);
    assert(d.bars[1].kind == 3 && d.bars[1].base == 0x400000000ULL);
    assert(d.bars[1].assigned && d.bars[1].prefetch && d.bars[1].raw_high == 4);
    assert(d.bars[2].kind == 6 && !d.bars[2].assigned);
    assert(d.bars[3].kind == 1 && d.bars[3].base == 0x3000);
    assert(d.bars[4].kind == 0 && !d.bars[4].assigned);
    assert(d.bars[5].kind == 4 && d.bars[5].base == 0x90000);
    d = pci_read_device(0,1,0);
    assert(d.bar_slots == 2 && d.primary == 0 && d.secondary == 9 && d.subordinate == 9);
    puts("PASS PCI topology/multifunction/bridge/identity/class/IRQ/BAR32/BAR64/IO");
    // Repeated inventory resets totals, and scanning empty fabric has no stale data.
    reset(); pci_init();
    assert(pci_count() == 0 && pci_target_gpu_count() == 0 && pci_target_nic_count() == 0);
    assert(pci_display_count() == 0 && pci_network_count() == 0 && final_devices == 0);
    assert(transactions == 256 * 32);
    puts("PASS PCI empty fabric and repeat inventory reset");
}

static void test_invalid_bars(void) {
    reset();
    u32 *p = device(0,0,0,0x1234,1,0,3);
    p[4] = 0x80000006; // reserved memory type
    p[5] = 0x00003003; // reserved IO flag
    p[6] = 0x01000002; // mem20 exceeds1MiB
    p[7] = 0x0000000C; p[8] = 0; // unassigned mem64, consume upper slot
    p[9] = 0x90000004; // missing upper half at last BAR
    pci_init();
    assert(pci_invalid_bar_count() == 4 && final_invalid == 4 && bar_lines == 4);
    struct PciDevice d = pci_read_device(0,0,0);
    assert(d.bars[0].kind == 5 && !d.bars[0].assigned);
    assert(d.bars[1].kind == 8 && !d.bars[1].assigned);
    assert(d.bars[2].kind == 8 && !d.bars[2].assigned);
    assert(d.bars[3].kind == 3 && d.bars[3].base == 0 && !d.bars[3].assigned);
    assert(d.bars[4].kind == 6 && d.bars[5].kind == 7);
    assert(reads[0][10] == 0); // malformed64 must not consume offset0x28
    p = device(2,0,0,0x1234,2,1,6);
    p[5] = 0x90000004; // bridge has only2BARs, cannot consume bus-number word
    d = pci_read_device(2,0,0);
    assert(d.bars[1].kind == 7 && !d.bars[1].assigned);
    assert(reads[bdf(2,0,0)][6] == 1); // exactly the normal bridge-number read
    puts("PASS PCI malformed/reserved/unassigned BARs and layout boundaries");
}

static void test_full_capacity(void) {
    reset();
    for (unsigned bus = 0; bus < 256; ++bus)
        for (unsigned slot = 0; slot < 32; ++slot)
            for (unsigned fn = 0; fn < 8; ++fn)
                device(bus,slot,fn,0x1234,0xABCD,0x80,0);
    pci_init();
    assert(pci_count() == 65536 && device_lines == 65536 && final_devices == 65536);
    assert(reads[65535][0] == 1 && pci_invalid_bar_count() == 0);
    puts("PASS PCI full 65536-function inventory without truncation");
}

static void test_bus_master(void) {
    reset();
    /* Un e1000 (8086:100E) con BAR0 de memoria y un vecino que no debe tocarse. */
    u32 *nic = device(0,2,0,0x8086,0x100E,0,2);
    nic[1] = 0x00100001;           /* Command: solo I/O Space; status arbitrario */
    nic[4] = 0x81040000;           /* BAR0 mem32 */
    u32 *other = device(0,3,0,0x8086,0x2922,0,1);
    other[1] = 0x00000007;
    pci_init();
    assert(pci_nic_present() && pci_nic_device() == 0x100E && pci_nic_bar0() == 0x81040000);
    assert(pci_nic_bus() == 0 && pci_nic_slot() == 2 && pci_nic_fn() == 0);
    assert(write_transactions == 0);   /* el inventario sigue siendo solo lectura */
    u64 command = pci_enable_bus_master(0, 2, 0);
    assert(command == 0x0007);          /* I/O + Memory Space + Bus Master */
    assert(write_transactions == 1 && writes[bdf(0,2,0)][1] == 1);
    assert(config[bdf(0,2,0)][1] == 0x00100007); /* status intacto, solo bits 1|2 */
    assert(config[bdf(0,3,0)][1] == 0x00000007);  /* el vecino no cambia */
    /* Repetir no vuelve a escribir: los bits ya estan puestos. */
    assert(pci_enable_bus_master(0, 2, 0) == 0x0007 && write_transactions == 1);
    /* BDF invalido: sin escritura. */
    assert(pci_enable_bus_master(256, 0, 0) == 0 && write_transactions == 1);
    /* Un NIC sin BAR0 de memoria asignado no se captura. */
    reset();
    u32 *bare = device(1,0,0,0x8086,0x15B8,0,2);
    (void)bare;
    pci_init();
    assert(!pci_nic_present() && pci_target_nic_count() == 1);
    puts("PASS PCI NIC capture + single bounded Command write (Memory Space | Bus Master)");
}

int main(void) {
    test_bounds(); test_scan(); test_invalid_bars(); test_full_capacity(); test_bus_master();
    puts("PASS all PCI host tests (synthetic devices; no physical GPU validation)");
    return 0;
}
