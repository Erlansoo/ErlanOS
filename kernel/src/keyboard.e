// L1 — teclado PS/2 (scancode set 1). Traduce a ASCII y encola; los servicios lo
// leen con la syscall getkey (capability Input).

import cpu;
import serial;
import pointer;

// Índice = scancode (0..57). Un 0 significa tecla sin carácter (shift, ctrl, F1...).
let KEYMAP: str = "\0\x1b" "1234567890-=" "\b\t" "qwertyuiop[]" "\n\0" "asdfghjkl;'`" "\0\\" "zxcvbnm,./" "\0*\0 ";
let KEYMAP_SHIFT: str = "\0\x1b" "!@#$%^&*()_+" "\b\t" "QWERTYUIOP{}" "\n\0" "ASDFGHJKL:\"~" "\0|" "ZXCVBNM<>?" "\0*\0 ";

const KEYMAP_LEN: u64 = 58;
const SC_LSHIFT: u8 = 0x2A;
const SC_RSHIFT: u8 = 0x36;
const QUEUE_LEN: u64 = 256;

var left_shift: bool = false;
var right_shift: bool = false;
var extended: bool = false;
var keys_total: u64 = 0;
var queue: [256]u8;
var q_head: u64 = 0;   // próximo a leer
var q_tail: u64 = 0;   // próximo a escribir
var cancel_pending: bool = false;

fn keyboard_init() {
    left_shift = false; right_shift = false; extended = false;
    q_head = 0; q_tail = 0; cancel_pending = false; keys_total = 0;
    kprintf("[kbd] ps/2 listo (controlador: i8042.e)\n");
}

fn keyboard_scancode(sc: u8) {
    if sc == 0xE0 { extended = true; return; }
    let is_extended = extended; extended = false;
    let released = (sc & 0x80) != 0;
    let code = sc & 0x7F;
    // PrintScreen's extended fake Shift must not change real modifiers.
    if code == SC_LSHIFT { if !is_extended { left_shift = !released; } return; }
    if code == SC_RSHIFT { if !is_extended { right_shift = !released; } return; }
    if released { return; }
    var ch: u8 = 0;
    if is_extended {
        if code == 0x4b { ch = 128; } if code == 0x4d { ch = 129; }
        if code == 0x47 { ch = 130; } if code == 0x4f { ch = 131; }
    }
    if ch == 0 && (code as u64) >= KEYMAP_LEN { return; }
    let shift = left_shift || right_shift;
    if ch == 0 { unsafe {
        if shift { ch = KEYMAP_SHIFT[code as u64]; } else { ch = KEYMAP[code as u64]; }
    } }
    // Reserved input event: Shift+Enter inserts LF; plain Enter submits.
    if code == 0x1C && shift { ch = 11; }
    if ch == 0 { return; }
    keys_total += 1;
    // Escape no compite con la cola (puede cancelar incluso si está llena).
    // Se mantiene activo hasta que el servicio lo reconoce explícitamente.
    if ch == 27 { cancel_pending = true; return; }
    let next = (q_tail + 1) % QUEUE_LEN;
    if next == q_head { return; }   // cola llena: se descarta
    queue[q_tail] = ch;
    q_tail = next;
}


// 0 si no hay tecla pendiente.
fn keyboard_getc() -> u8 {
    if cancel_pending { cancel_pending = false; return 27; }
    if q_head == q_tail { return 0; }
    let ch = queue[q_head];
    q_head = (q_head + 1) % QUEUE_LEN;
    return ch;
}

fn keyboard_cancelled(clear: bool) -> bool {
    let pending = cancel_pending;
    if clear { cancel_pending = false; }
    return pending;
}

fn keyboard_keys_total() -> u64 { return keys_total; }
