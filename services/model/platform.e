// Plataforma para el runtime del modelo dentro de ErlanOS: todo pasa por syscalls con
// capability. services/model/host/platform.e ofrece la misma interfaz sobre el anfitrión
// para las pruebas; cuál se usa lo decide el -I del compilador.
// Sustituye a services/model/platform.h (C) desde el 26/10/2026.
import usys;

const CAP_LOG_SLOT: u64 = 0;
const CAP_TIME_SLOT: u64 = 1;
const CAP_CONSOLE_SLOT: u64 = 3;
const CAP_INPUT_SLOT: u64 = 4;
const CAP_MODULE_SLOT: u64 = 5;
const CAP_MEMORY_SLOT: u64 = 6;

fn platform_write(p: ptr<u8>, n: u64) { unsafe { usys(8, CAP_CONSOLE_SLOT, p as u64, n); } }
fn platform_log(s: str) {
    var n: u64 = 0;
    unsafe { while s[n] != 0 { n += 1; } usys(1, CAP_LOG_SLOT, s as ptr<u8> as u64, n); }
}
fn platform_now_ms() -> u64 { return usys(7, CAP_TIME_SLOT, 0, 0); }
fn platform_getkey() -> u8 { return usys(9, CAP_INPUT_SLOT, 0, 0) as u8; }
fn platform_cancelled() -> bool { return usys(17, CAP_INPUT_SLOT, 0, 0) == 1; }
fn platform_clear_cancel() { usys(17, CAP_INPUT_SLOT, 1, 0); }
fn platform_halo(state: u64) { usys(13, CAP_CONSOLE_SLOT, state, 0); }
fn platform_yield() { usys(2, 0, 0, 0); }
fn platform_sleep_ms(ms: u64) { usys(4, ms, 0, 0); }
fn platform_map_module(idx: u64) -> u64 { return usys(10, CAP_MODULE_SLOT, idx, 0); }
fn platform_module_size(idx: u64) -> u64 { return usys(11, CAP_MODULE_SLOT, idx, 0); }
fn platform_map_anon(pages: u64) -> u64 { return usys(12, CAP_MEMORY_SLOT, pages, 0); }
