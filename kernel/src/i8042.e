// L1 — controlador de teclado y ratón PS/2 (i8042): el mecanismo común que antes estaba
// repartido entre keyboard.e y pointer.e. Secuencia tomada de la lectura de Linux
// drivers/input/serio/i8042.c (GPL-2.0, sólo leído; esta implementación es original a partir
// de la referencia del 8042 de IBM/Intel): leer el byte de control hasta que dos lecturas
// coincidan, deshabilitar el teclado mientras se configura, respetar o fijar la traducción a
// scancodes set 1, vaciar el buffer con un límite, comprobar el puerto auxiliar con el bucle
// 0xD3 y la prueba 0xA9, nunca colgarse: cada espera tiene plazo. Diferencia buscada: Linux
// respeta un BIOS que dejó la traducción apagada (sabe leer set 2); keyboard.e sólo entiende
// set 1, así que aquí la traducción se enciende y se dice.
import cpu;
import acpi;
import keyboard;
import pointer;
import serial;

const DATA: u16 = 0x60;
const STATUS: u16 = 0x64;
const ST_OBF: u8 = 0x01;
const ST_IBF: u8 = 0x02;
const ST_KEYLOCK: u8 = 0x10;     // 0 = teclado bloqueado
const ST_AUXDATA: u8 = 0x20;
const ST_TIMEOUT: u8 = 0x40;
const ST_PARITY: u8 = 0x80;
const CTR_KBDINT: u8 = 0x01;
const CTR_AUXINT: u8 = 0x02;
const CTR_KBDDIS: u8 = 0x10;
const CTR_AUXDIS: u8 = 0x20;
const CTR_XLATE: u8 = 0x40;
const CMD_READ_CTR: u8 = 0x20;
const CMD_WRITE_CTR: u8 = 0x60;
const CMD_AUX_DISABLE: u8 = 0xA7;
const CMD_AUX_ENABLE: u8 = 0xA8;
const CMD_AUX_TEST: u8 = 0xA9;
const CMD_AUX_LOOP: u8 = 0xD3;
const CMD_AUX_SEND: u8 = 0xD4;
const SPINS: u64 = 200000;          // ~decenas de ms por espera; nunca sin plazo
const FLUSH_LIMIT: u64 = 16;         // como I8042_BUFFER_SIZE en Linux

var present: bool = false;
var aux_present: bool = false;
var initial_ctr: u8 = 0;
var ctr: u8 = 0;
var kbd_bytes: u64 = 0;
var aux_bytes: u64 = 0;

fn i8042_present() -> bool { return present; }
fn i8042_aux_present() -> bool { return aux_present; }

fn wait_write() -> bool {
    for i in 0..SPINS { if (inb(STATUS) & ST_IBF) == 0 { return true; } cpu_pause(); }
    return false;
}
fn wait_read() -> bool {
    for i in 0..SPINS { if (inb(STATUS) & ST_OBF) != 0 { return true; } cpu_pause(); }
    return false;
}
// Lee un byte (256 si no llega a tiempo).
fn read_byte() -> u64 { if !wait_read() { return 256; } return inb(DATA) as u64; }
fn command(c: u8) -> bool { if !wait_write() { return false; } outb(STATUS, c); return true; }
fn write_data(b: u8) -> bool { if !wait_write() { return false; } outb(DATA, b); return true; }

// Vacía el buffer de salida; false si sigue lleno tras 16 lecturas (no hay controlador).
fn flush() -> bool {
    var n: u64 = 0;
    while (inb(STATUS) & ST_OBF) != 0 {
        if n >= FLUSH_LIMIT { return false; }
        inb(DATA); n += 1;
    }
    return true;
}

fn read_ctr() -> u64 { if !command(CMD_READ_CTR) { return 256; } return read_byte(); }
fn write_ctr(v: u8) -> bool { if !command(CMD_WRITE_CTR) { return false; } if !write_data(v) { return false; } ctr = v; return true; }

// Envía un byte al ratón y devuelve su respuesta (256 si no responde).
fn i8042_aux_command(b: u8) -> u64 {
    if !command(CMD_AUX_SEND) || !write_data(b) { return 256; }
    return read_byte();
}
fn i8042_aux_read() -> u64 { return read_byte(); }

// Comprobación del puerto auxiliar como en Linux: bucle 0xD3 (debe devolver el mismo byte con
// AUXDATA), si no la prueba 0xA9 (0x00, 0xFA o 0xFF valen), y el bit AUXDIS con 0xA7/0xA8.
fn check_aux() -> bool {
    flush();
    var ok = false;
    if command(CMD_AUX_LOOP) && write_data(0x5A) {
        if wait_read() {
            let st = inb(STATUS); let b = inb(DATA) as u64;
            ok = b == 0x5A && (st & ST_AUXDATA) != 0;
        }
    }
    if !ok {
        if !command(CMD_AUX_TEST) { return false; }
        let r = read_byte();
        if r != 0x00 && r != 0xFA && r != 0xFF { return false; }
    }
    if !command(CMD_AUX_DISABLE) { return false; }
    var c = read_ctr(); if c > 255 { return false; }
    if ((c as u8) & CTR_AUXDIS) == 0 { kprintf("[i8042] el bit AUXDIS no se activa (SiS?)\n"); }
    if !command(CMD_AUX_ENABLE) { return false; }
    c = read_ctr(); if c > 255 { return false; }
    if ((c as u8) & CTR_AUXDIS) != 0 { return false; }
    return true;
}

// Inicializa el controlador. Devuelve false, sin tocar nada más, si no existe.
fn i8042_init() -> bool {
    present = false; aux_present = false; kbd_bytes = 0; aux_bytes = 0;
    if !acpi_has_8042() {
        kprintf("[i8042] la FADT dice que no hay 8042: no se toca (teclado USB sin emulacion)\n");
        return false;
    }
    if inb(STATUS) == 0xFF || !flush() {
        kprintf("[i8042] sin controlador (estado 0xFF o buffer que no se vacia)\n");
        return false;
    }
    // Byte de control estable: dos lecturas iguales (hasta 10 intentos).
    var stable: u64 = 256;
    for i in 0..10 {
        let a = read_ctr(); let b = read_ctr();
        if a <= 255 && a == b { stable = a; break; }
    }
    if stable > 255 { kprintf("[i8042] no se obtiene un byte de control estable\n"); return false; }
    initial_ctr = stable as u8;
    var c = initial_ctr;
    c = (c | CTR_KBDDIS) & ~CTR_KBDINT;              // teclado quieto mientras se configura
    if (inb(STATUS) & ST_KEYLOCK) == 0 { kprintf("[i8042] aviso: bloqueo de teclado activo\n"); }
    var forced_xlate = false;
    if (c & CTR_XLATE) == 0 { c = c | CTR_XLATE; forced_xlate = true; }   // keyboard.e usa set 1
    if !write_ctr(c) { kprintf("[i8042] no se puede escribir el byte de control\n"); return false; }
    flush();
    present = true;
    aux_present = check_aux();
    // Teclado: primero se habilita su puerto (sin IRQ), como hace Linux antes de hablarle (un
    // 8042 real no manda nada por un puerto con KBDDIS); luego reinicio (0xFF -> 0xFA, 0xAA),
    // no fatal; y al final la IRQ.
    var kbd_reset = false;
    if !write_ctr(ctr & ~CTR_KBDDIS) { kprintf("[i8042] no se puede habilitar el puerto del teclado\n"); }
    flush();
    if write_data(0xFF) {
        let ack = read_byte();
        if ack == 0xFA { let bat = read_byte(); kbd_reset = bat == 0xAA; }
    }
    flush();
    c = ctr | CTR_KBDINT;
    if !write_ctr(c) { kprintf("[i8042] no se puede habilitar la IRQ del teclado\n"); }
    kprintf("[i8042] controlador presente: ctr inicial=%x ahora=%x traduccion_forzada=%d aux=%d teclado_reset=%d\n",
            initial_ctr as u64, ctr as u64, forced_xlate as u64, aux_present as u64, kbd_reset as u64);
    return true;
}

// Habilita el puerto auxiliar con su interrupción (lo pide pointer.e cuando configura el ratón).
fn i8042_enable_aux() -> bool {
    if !present || !aux_present { return false; }
    return write_ctr((ctr & ~CTR_AUXDIS) | CTR_AUXINT);
}

// Vacía lo que quede en el buffer de salida. Se llama justo antes de habilitar las
// interrupciones: una entrada del IOAPIC por flanco no ve un byte que ya estaba esperando (la
// línea ya estaba alta), y el 8042 no acepta el siguiente hasta que se lea ése.
fn i8042_drain() { if present { flush(); } }

// Interrupción compartida de IRQ1 e IRQ12: el bit AUXDATA del estado dice de qué puerto es.
// Se leen todos los bytes disponibles (hasta 16): dos flancos muy seguidos pueden llegar como
// una sola interrupción. El primer byte de cada puerto se anuncia por serial: es la evidencia
// de que la ruta (IOAPIC o PIC) entrega la interrupción.
fn i8042_irq() {
    for n in 0..FLUSH_LIMIT {
        let st = inb(STATUS);
        if (st & ST_OBF) == 0 { return; }
        let b = inb(DATA);
        if (st & ST_AUXDATA) != 0 {
            aux_bytes += 1;
            if aux_bytes == 1 { kprintf("[i8042] primer byte del raton por interrupcion (%x)\n", b as u64); }
            pointer_byte(b);
        } else if (st & (ST_TIMEOUT | ST_PARITY)) == 0 {     // un byte dudoso se descarta
            kbd_bytes += 1;
            if kbd_bytes == 1 { kprintf("[i8042] primer byte del teclado por interrupcion (%x)\n", b as u64); }
            keyboard_scancode(b);
        }
    }
}
fn i8042_kbd_bytes() -> u64 { return kbd_bytes; }
fn i8042_aux_bytes() -> u64 { return aux_bytes; }
