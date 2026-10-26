// Plataforma de anfitrión para probar el runtime del modelo fuera de ErlanOS. Misma interfaz
// que services/model/platform.e; la salida, el registro, el reloj y la cancelación pasan por
// cuatro ganchos que define cada arnés en C (host/main.c, host/chat_test.c, host/plan.c:
// host_write, host_log, host_now_ms, host_cancelled), porque stdout/stderr y CLOCKS_PER_SEC
// son macros de C. Sustituye a services/model/host/platform.h desde el 26/10/2026.
extern c "stdio.h" { fn getchar() -> i32; }
extern c {
    fn host_write(p: ptr<u8>, n: u64);
    fn host_log(s: str);
    fn host_now_ms() -> u64;
    fn host_cancelled() -> bool;
}

fn platform_write(p: ptr<u8>, n: u64) { host_write(p, n); }
fn platform_log(s: str) { host_log(s); }
fn platform_now_ms() -> u64 { return host_now_ms(); }
fn platform_getkey() -> u8 { let c = getchar(); if c < 0 { return 0; } return c as u8; }
fn platform_cancelled() -> bool { return host_cancelled(); }
fn platform_clear_cancel() { }
fn platform_halo(state: u64) { }
fn platform_yield() { }
fn platform_sleep_ms(ms: u64) { }
fn platform_map_module(idx: u64) -> u64 { return 0; }
fn platform_module_size(idx: u64) -> u64 { return 0; }
fn platform_map_anon(pages: u64) -> u64 { return 0; }
