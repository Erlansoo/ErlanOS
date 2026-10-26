// L1 — primitivas mecánicas de M4. Las decisiones y el protocolo AI viven en L2.
import pit;
import pmm;
import sched;
import syscall;
import serial;
import console;

const ERR_ARG: u64 = 0xFFFFFFFFFFFFFFFE;
var mode: u64 = 0;
var revision: u64 = 0;
var last_op: u64 = 0;
var last_receipt: u64 = 0;
var panel_shown: bool = false;
fn system_document_shown() { panel_shown = false; }

// 0 uptime(ms), 1 páginas libres, 2 límite físico en páginas, 3 hilos vivos,
// 4 cambios de contexto, 5 llamadas denegadas, 6 modo, 7 revisión del modo.
fn system_inspect(metric: u64) -> u64 {
    match metric {
        0 => { return pit_uptime_ms(); }
        1 => { return pmm_free_count(); }
        2 => { return pmm_total_count(); }
        3 => { return sched_live_count(); }
        4 => { return sched_switches(); }
        5 => { return syscall_denied_count(); }
        6 => { return mode; }
        7 => { return revision; }
        _ => { return ERR_ARG; }
    }
    return ERR_ARG;
}

fn system_present() {
    panel_shown = true;
    console_present_status(mode, revision, pit_uptime_ms(), pmm_free_count(),
                           pmm_total_count(), sched_live_count(), sched_switches(),
                           syscall_denied_count(), last_op, last_receipt);
}

fn system_present_receipt(op: u64, receipt: u64) -> u64 {
    if op < 1 || op > 4 || receipt == 0 || receipt <= last_receipt { return ERR_ARG; }
    last_op = op;
    last_receipt = receipt;
    kprintf("[present] op=%d recibo=%d\n", op, receipt);
    system_present();
    return 0;
}

fn system_halo_changed() {
    if panel_shown { system_present(); }
}

// Idempotente: repetir el mismo modo no cuenta como otra mutación.
fn system_set_mode(next: u64) -> u64 {
    if next > 1 { return ERR_ARG; }
    if mode != next {
        mode = next;
        revision += 1;
        console_set_mode(mode);
        system_present();
    }
    return revision;
}
