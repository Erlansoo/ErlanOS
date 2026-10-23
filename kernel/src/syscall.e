// L1 — syscalls por `int 0x80`. Convención: rax = número, rdi/rsi/rdx = argumentos,
// rax = retorno. Todo efecto exige una capability del hilo actual.

extern c "sched.h" {
    fn sched_current_id() -> u64;
    fn sched_check_cap(slot: u64, kind: u32, rights: u32) -> bool;
    fn sched_cap_obj(slot: u64) -> u64;
    fn sched_yield();
    fn sched_sleep(ticks: u64);
    fn sched_exit(code: u64);
    fn sched_thread_name(id: u64) -> str;
    fn sched_current_cr3() -> u64;
}
extern c "vmm.h" { fn vmm_user_range_ok(cr3: u64, p: u64, len: u64, writable: bool) -> bool; }
extern c "system.h" {
    fn system_inspect(metric: u64) -> u64;
    fn system_set_mode(mode: u64) -> u64;
    fn system_present();
    fn system_present_receipt(op: u64, receipt: u64) -> u64;
    fn system_halo_changed();
    fn system_document_shown();
}
extern c "ipc.h" {
    fn ipc_send(ep: u64, src: u64, len: u64) -> u64;
    fn ipc_recv(ep: u64, dst: u64, cap: u64) -> u64;
}
extern c "proc.h" {
    fn proc_map_module(idx: u64) -> u64;
    fn proc_module_size(idx: u64) -> u64;
    fn proc_map_anon(id: u64, pages: u64) -> u64;
}
extern c "console.h" { fn console_write(p: ptr<u8>, len: u64); fn console_set_halo(state: u64); }
extern c "document_view.h" { fn document_view(p: ptr<u8>, len: u64) -> bool; }
extern c "keyboard.h" { fn keyboard_getc() -> u8; fn keyboard_cancelled(clear: bool) -> bool; }
extern c "pit.h" { fn pit_uptime_ms() -> u64; fn pit_ticks() -> u64; }
extern c "serial.h" { fn kprintf(fmt: str, ...); fn serial_putc(c: u8); }
extern c "storage.h" { fn storage_size() -> u64; fn storage_io(op: u64, sector: u64, p: ptr<u8>) -> bool; }
extern c "pointer.h" { fn pointer_get() -> u64; }
extern c "workspace_view.h" { fn workspace_view(p: ptr<u8>,len: u64) -> bool; fn workspace_geometry() -> u64; fn workspace_hit(x: u64,y: u64) -> u64; fn desktop_begin(); fn desktop_end(); }

const SYS_EXIT: u64 = 0;
const SYS_LOG: u64 = 1;          // (cap, ptr, len)      serial
const SYS_YIELD: u64 = 2;
const SYS_GETPID: u64 = 3;
const SYS_SLEEP_MS: u64 = 4;     // (ms)
const SYS_SEND: u64 = 5;         // (cap, ptr, len)
const SYS_RECV: u64 = 6;         // (cap, ptr, max) -> len
const SYS_UPTIME_MS: u64 = 7;    // (cap)
const SYS_CONSOLE: u64 = 8;      // (cap, ptr, len)      pantalla
const SYS_GETKEY: u64 = 9;       // (cap) -> tecla o 0
const SYS_MAP_MODULE: u64 = 10;  // (cap, idx) -> virt (0 si no existe)
const SYS_MODULE_SIZE: u64 = 11; // (cap, idx) -> bytes
const SYS_MAP_ANON: u64 = 12;    // (cap, pages) -> virt
const SYS_HALO: u64 = 13;        // (cap, state)
const SYS_INSPECT: u64 = 14;     // (cap_system, metric, index=0) -> valor escalar
const SYS_SET_MODE: u64 = 15;    // (cap_system, mode, 0) -> revisión
const SYS_PRESENT: u64 = 16;     // (cap_console, kind=1, receipt*16+op or 0) -> panel
const SYS_CANCEL: u64 = 17;      // (cap_input, clear=0/1, 0) -> Escape pendiente
const SYS_DOCUMENT: u64 = 18;    // (cap_console RIGHT_PRESENT, ptr, length) -> bounded page surface

const CAP_LOG: u32 = 1;
const CAP_TIME: u32 = 2;
const CAP_ENDPOINT: u32 = 3;
const CAP_CONSOLE: u32 = 4;
const CAP_INPUT: u32 = 5;
const CAP_MODULE: u32 = 6;
const CAP_MEMORY: u32 = 7;
const CAP_SYSTEM: u32 = 8;
const RIGHT_SEND: u32 = 1;
const RIGHT_RECV: u32 = 2;
const RIGHT_READ: u32 = 1;
const RIGHT_WRITE: u32 = 2;
const RIGHT_PRESENT: u32 = 1;
const RIGHT_EGRESS: u32 = 2;     // nunca se concede

const ERR_CAP: u64 = 0xFFFFFFFFFFFFFFFF;    // -1: sin capability
const ERR_ARG: u64 = 0xFFFFFFFFFFFFFFFE;    // -2: argumento inválido

var denied: u64 = 0;

fn user_range_ok(p: u64, len: u64, writable: bool) -> bool {
    return vmm_user_range_ok(sched_current_cr3(), p, len, writable);
}

fn deny(what: str, slot: u64) -> u64 {
    denied += 1;
    kprintf("[cap] DENEGADO: hilo %d '%s' intento %s con slot %d\n",
            sched_current_id(), sched_thread_name(sched_current_id()), what, slot);
    return ERR_CAP;
}

fn sys_log(slot: u64, p: u64, len: u64) -> u64 {
    if !sched_check_cap(slot, CAP_LOG, 0) { return deny("log", slot); }
    if len > 4096 || !user_range_ok(p, len, false) { return ERR_ARG; }
    kprintf("[%s] ", sched_thread_name(sched_current_id()));
    unsafe {
        let s = p as ptr<u8>;
        for i in 0..len { serial_putc(s[i]); }
    }
    serial_putc(10);
    return len;
}

fn sys_console(slot: u64, p: u64, len: u64) -> u64 {
    if !sched_check_cap(slot, CAP_CONSOLE, 0) { return deny("console", slot); }
    if len > 65536 || !user_range_ok(p, len, false) { return ERR_ARG; }
    desktop_begin(); unsafe { console_write(p as ptr<u8>, len); } desktop_end();
    return len;
}

// Entrada desde idt.e con el frame del hilo: rax, rdi, rsi, rdx.
fn syscall_dispatch(nr: u64, a: u64, b: u64, c: u64) -> u64 {
    match nr {
        22 => {
            if !sched_check_cap(a,CAP_INPUT,0) { return deny("pointer",a); }
            if b>2 { return ERR_ARG; }
            if b==1 { return workspace_geometry(); }
            if b==2 { return workspace_hit(c&65535,c>>16); }
            if c!=0 { return ERR_ARG; }
            desktop_begin(); desktop_end(); return pointer_get();
        }
        23 => {
            if !sched_check_cap(a,CAP_CONSOLE,RIGHT_PRESENT) { return deny("workspace_view",a); }
            if b==0 && c==0 { return workspace_geometry(); }
            var length=c; if b==0 && c==1 { length=0; }
            if length>2048 || (length!=0 && !user_range_ok(b,length,false)) { return ERR_ARG; }
            desktop_begin(); var ok=false;
            unsafe { ok=workspace_view(b as ptr<u8>,length); } desktop_end();
            if ok { return 0; } return ERR_ARG;
        }
        21 => {
            let op = c & 3; let sector = c >> 2;
            var rights: u32 = 1; if op >= 2 { rights = 2; }
            if !sched_check_cap(a, 10, rights) || sched_cap_obj(a) != 0 { return deny("storage", a); }
            if op == 0 { if b != 0 || c != 0 { return ERR_ARG; } return storage_size(); }
            if op == 3 { if b != 0 || sector != 0 { return ERR_ARG; } }
            else if !user_range_ok(b, 512, op == 1) { return ERR_ARG; }
            unsafe { if storage_io(op, sector, b as ptr<u8>) { return 0; } }
            return ERR_ARG;
        }
        SYS_EXIT => { sched_exit(a); return 0; }
        SYS_LOG => { return sys_log(a, b, c); }
        SYS_YIELD => { sched_yield(); return 0; }
        SYS_GETPID => { return sched_current_id(); }
        SYS_SLEEP_MS => {
            var t = a / 10;
            if t == 0 { t = 1; }
            sched_sleep(t);
            return 0;
        }
        SYS_SEND => {
            if !sched_check_cap(a, CAP_ENDPOINT, RIGHT_SEND) { return deny("send", a); }
            if c > 256 || !user_range_ok(b, c, false) { return ERR_ARG; }
            return ipc_send(sched_cap_obj(a), b, c);
        }
        SYS_RECV => {
            if !sched_check_cap(a, CAP_ENDPOINT, RIGHT_RECV) { return deny("recv", a); }
            if c > 256 || !user_range_ok(b, c, true) { return ERR_ARG; }
            return ipc_recv(sched_cap_obj(a), b, c);
        }
        SYS_UPTIME_MS => {
            if !sched_check_cap(a, CAP_TIME, 0) { return deny("uptime", a); }
            return pit_uptime_ms();
        }
        SYS_CONSOLE => { return sys_console(a, b, c); }
        SYS_DOCUMENT => {
            if !sched_check_cap(a, CAP_CONSOLE, RIGHT_PRESENT) { return deny("document", a); }
            if c == 0 && b != 0 { return ERR_ARG; }
            if c > 32768 || (c != 0 && !user_range_ok(b, c, false)) { return ERR_ARG; }
            desktop_begin(); var ok=false;
            unsafe { ok=document_view(b as ptr<u8>, c); } desktop_end();
            if !ok { return ERR_ARG; }
            system_document_shown();
            return 0;
        }
        SYS_GETKEY => {
            if !sched_check_cap(a, CAP_INPUT, 0) { return deny("getkey", a); }
            return keyboard_getc() as u64;
        }
        SYS_CANCEL => {
            if !sched_check_cap(a, CAP_INPUT, 0) { return deny("cancel", a); }
            if b > 1 || c != 0 { return ERR_ARG; }
            if keyboard_cancelled(b == 1) { return 1; }
            return 0;
        }
        SYS_MAP_MODULE => {
            if !sched_check_cap(a, CAP_MODULE, 0) { return deny("map_module", a); }
            return proc_map_module(b);
        }
        SYS_MODULE_SIZE => {
            if !sched_check_cap(a, CAP_MODULE, 0) { return deny("module_size", a); }
            return proc_module_size(b);
        }
        SYS_MAP_ANON => {
            if !sched_check_cap(a, CAP_MEMORY, 0) { return deny("map_anon", a); }
            return proc_map_anon(sched_current_id(), b);
        }
        SYS_HALO => {
            if !sched_check_cap(a, CAP_CONSOLE, 0) { return deny("halo", a); }
            if b > 5 { return ERR_ARG; }
            if b == 3 && !sched_check_cap(a, CAP_CONSOLE, RIGHT_PRESENT) { return deny("halo acting", a); }
            if b == 4 && !sched_check_cap(a, CAP_CONSOLE, RIGHT_PRESENT) { return deny("halo authorization", a); }
            if b == 5 && !sched_check_cap(a, CAP_CONSOLE, RIGHT_EGRESS) { return deny("halo cloud", a); }
            desktop_begin(); console_set_halo(b);
            kprintf("[halo] estado=%d\n", b);
            system_halo_changed();
            desktop_end();
            return 0;
        }
        SYS_INSPECT => {
            if !sched_check_cap(a, CAP_SYSTEM, RIGHT_READ) { return deny("inspect", a); }
            if sched_cap_obj(a) != 0 { return deny("inspect object", a); }
            if c != 0 || b > 7 { return ERR_ARG; }
            return system_inspect(b);
        }
        SYS_SET_MODE => {
            if !sched_check_cap(a, CAP_SYSTEM, RIGHT_WRITE) { return deny("set_mode", a); }
            if sched_cap_obj(a) != 0 { return deny("set_mode object", a); }
            if c != 0 || b > 1 { return ERR_ARG; }
            desktop_begin(); let result=system_set_mode(b); desktop_end(); return result;
        }
        SYS_PRESENT => {
            if !sched_check_cap(a, CAP_CONSOLE, RIGHT_PRESENT) { return deny("present", a); }
            if b != 1 { return ERR_ARG; }
            desktop_begin(); var result: u64=0;
            if c != 0 { result=system_present_receipt(c & 15,c>>4); }
            else { system_present(); }
            desktop_end(); return result;
        }
        _ => {
            kprintf("[sys] syscall desconocida %d de hilo %d\n", nr, sched_current_id());
            return ERR_ARG;
        }
    }
    return ERR_ARG;
}

fn syscall_denied_count() -> u64 { return denied; }
