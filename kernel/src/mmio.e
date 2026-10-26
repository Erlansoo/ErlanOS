// L1 — accesos MMIO y barreras para los controladores de dispositivo. Cada acceso es una
// instrucción de movimiento con clobber de memoria: clang no puede reordenarlo, elevarlo
// ni combinarlo, que es lo que exigen los registros y los descriptores DMA que escribe el
// hardware. Sustituye a mmio.h (C) desde el 26/10/2026.

fn mmio_read8(a: u64) -> u8 { var v: u8 = 0; unsafe { asm("movb (%1), %0" : "=r"(v) : "r"(a) : "memory"); } return v; }
fn mmio_read16(a: u64) -> u16 { var v: u16 = 0; unsafe { asm("movw (%1), %0" : "=r"(v) : "r"(a) : "memory"); } return v; }
fn mmio_read32(a: u64) -> u32 { var v: u32 = 0; unsafe { asm("movl (%1), %0" : "=r"(v) : "r"(a) : "memory"); } return v; }
fn mmio_read64(a: u64) -> u64 { var v: u64 = 0; unsafe { asm("movq (%1), %0" : "=r"(v) : "r"(a) : "memory"); } return v; }
fn mmio_write8(a: u64, v: u8) { unsafe { asm("movb %0, (%1)" : : "r"(v), "r"(a) : "memory"); } }
fn mmio_write16(a: u64, v: u16) { unsafe { asm("movw %0, (%1)" : : "r"(v), "r"(a) : "memory"); } }
fn mmio_write32(a: u64, v: u32) { unsafe { asm("movl %0, (%1)" : : "r"(v), "r"(a) : "memory"); } }
fn mmio_write64(a: u64, v: u64) { unsafe { asm("movq %0, (%1)" : : "r"(v), "r"(a) : "memory"); } }

// mfence es base de x86-64 (SSE2 garantizado); ordena cargas y almacenamientos antes de
// tocar un registro doorbell (TDT/RDT). pause relaja los bucles de espera.
fn cpu_mfence() { unsafe { asm("mfence" : : : "memory"); } }
fn cpu_pause() { unsafe { asm("pause" : : : "memory"); } }
