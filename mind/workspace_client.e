// Typed workspace adapter. Project recognition proposes a switch; confirmation
// is handled by the native explorer, never inferred from elapsed time.
import ai_wire;
import ulib;
import usys;
import platform;
import document_client;
import chat;
var wc_id: u64=0; var wc_reply: [256]u8; var wc_visible: bool=false; var wc_dialog: u64=0;
var wc_epoch: u64=0; var wc_loaded_epoch: u64=0; var wc_project: u64=0; var wc_count: u64=0;
var wc_button: u64=0; var wc_click: u64=0; var wc_click_time: u64=0;
var wc_pending: [256]u8; var wc_pending_n: u64=0; var wc_pending_project: u64=0;
var wc_history: [32768]u8; var wc_history_n: u64=0;
fn wc_empty() -> []u8 { return wc_pending[0..0]; }
fn wc_write(s: str) { unsafe { platform_write(s as ptr<u8>,ustrlen(s)); } }
fn wc_request(op: u64,a: u64,b: u64,data: []u8) -> bool {
    if data.len>184 { return false; }
    var req: [256]u8; for i in 0..256 { req[i]=0; } wc_id+=1;
    ai_put64(req[..],0,0x31505357); ai_put64(req[..],8,wc_id); ai_put64(req[..],16,op);
    ai_put64(req[..],32,a); ai_put64(req[..],40,b); ai_put64(req[..],64,data.len);
    for i in 0..data.len { req[72+i]=data[i]; }
    if usend(8,req[0..72+data.len])!=0 || urecv(9,wc_reply[..])!=256 { return false; }
    let r=wc_reply[..];
    if ai_get64(r,0)!=0x31505357 || ai_get64(r,8)!=wc_id || ai_get64(r,16)!=op { return false; }
    wc_visible=ai_get64(r,40)==1; wc_dialog=ai_get64(r,48); wc_project=ai_get64(r,64); wc_epoch=ai_get64(r,32); wc_count=ai_get64(r,96);
    if ai_get64(r,72)!=0 { document_external_opened(); }
    if wc_dialog==0 && wc_pending_n!=0 && wc_project!=wc_pending_project { wc_pending_n=0; }
    return ai_get64(r,24)==0;
}
fn wc_restore() {
    if wc_loaded_epoch==wc_epoch { return; }
    wc_loaded_epoch=wc_epoch; wc_history_n=0;
    loop {
        if !wc_request(12,wc_history_n,0,wc_empty()) { model_reset_context(); wc_write("No se pudo recuperar la conversacion.\n"); return; }
        let n=ai_get64(wc_reply[..],120); let total=ai_get64(wc_reply[..],112);
        if n>128 || total>32768 || wc_history_n+n>total { model_reset_context(); return; }
        for i in 0..n { wc_history[wc_history_n+i]=wc_reply[128+i]; } wc_history_n+=n;
        if wc_history_n==total { break; } if n==0 { model_reset_context(); return; }
    }
    ulog_num("workspace: historial bytes=",wc_history_n);
    platform_clear_cancel(); platform_halo(2);
    wc_write("\n");
    if wc_project==0 { wc_write("Chat general\n"); }
    else {
        wc_write("Proyecto: "); wc_request(11,wc_project,0,wc_empty());
        unsafe { platform_write(&wc_reply[128],ai_get64(wc_reply[..],120)); } wc_write("\n");
    }
    // Show the last complete messages; older records remain saved in the journal.
    var at: u64=0; var start: u64=0;
    while at+16<=wc_history_n { let len=ai_get64(wc_history[..],at+8); if len>wc_history_n-at-16 { break; } if wc_history_n-at>1800 { start=at+16+len; } at+=16+len; }
    at=start;
    while at+16<=wc_history_n {
        let role=ai_get64(wc_history[..],at); let len=ai_get64(wc_history[..],at+8); if len>wc_history_n-at-16 { break; }
        if role==1 { wc_write("Tu: "); } else { wc_write("Erlan: "); }
        unsafe { platform_write(&wc_history[at+16],len); } wc_write("\n"); at+=16+len;
    }
    if !model_restore_conversation(wc_history[0..wc_history_n]) { wc_write("Se abrio el historial; el contexto local no termino de cargarse.\n"); }
    platform_clear_cancel(); platform_halo(0); ulog("workspace: contexto restaurado");
}
fn workspace_client_init() { wc_request(0,0,0,wc_empty()); }
fn workspace_poll() {
    // Drain bounded queued PS/2 events even when the keyboard is idle.
    for i in 0..128 {
        let event=usys(22,4,0,0); if event==0 || event>=0xfffffffffffffffe { break; }
        let button=(event>>32)&1;
        if button==1 && wc_button==0 {
            let action=usys(22,4,2,event&0xffffffff); let now=platform_now_ms();
            var is_double: u64=0; if action>=100 && action==wc_click && now-wc_click_time<=500 { is_double=1; }
            wc_click=action; wc_click_time=now; if is_double==1 { wc_click=0; }
            if action!=0 { wc_request(8,action,is_double,wc_empty()); }
        }
        wc_button=button;
    }
    wc_restore();
    if wc_pending_n!=0 && !wc_visible && wc_project==wc_pending_project {
        let n=wc_pending_n; wc_pending_n=0; workspace_chat(wc_pending[0..n]);
        wc_write("\n\x0e> "); ulog("m4: command done");
    }
}
fn workspace_key(c: u8) -> bool {
    if !wc_visible { return false; }
    wc_request(9,c as u64,0,wc_empty()); wc_restore(); return true;
}
fn wc_fold(c: u8) -> u8 { if c>=65 && c<=90 { return c+32; } return c; }
fn wc_prefix(line: []u8,s: str) -> bool {
    let n=ustrlen(s); if line.len<n { return false; }
    unsafe { for i in 0..n { if wc_fold(line[i])!=s[i] { return false; } } } return true;
}
fn wc_exact(line: []u8,s: str) -> bool { return line.len==ustrlen(s) && wc_prefix(line,s); }
fn wc_run(op: u64,data: []u8) {
    if !wc_request(op,0,0,data) {
        let status=ai_get64(wc_reply[..],24);
        if status==3 { wc_write("Ese nombre ya existe. Usa otro nombre.\n"); }
        else if status==1 { wc_write("No hay un volumen de trabajos disponible para guardar.\n"); }
        else if status==6 { wc_write("No encontre ese nombre en la carpeta actual.\n"); }
        else { wc_write("No se pudo completar. Revisa el explorador.\n"); }
    } else if op==5 && wc_dialog==0 { wc_write("Documento guardado.\n"); }
    else if op==3 && wc_dialog==0 { wc_write("Carpeta creada.\n"); }
}
fn workspace_command(line: []u8) -> bool {
    if wc_exact(line,"/archivos") || wc_exact(line,"abre el explorador") || wc_exact(line,"mis archivos") { wc_run(1,wc_empty()); return true; }
    if wc_exact(line,"/guardar") || wc_exact(line,"guarda el documento") || wc_exact(line,"guardar documento") || wc_exact(line,"/doc guardar") { wc_run(5,wc_empty()); return true; }
    if wc_prefix(line,"/guardar ") { wc_run(5,line[9..]); return true; }
    if wc_prefix(line,"/doc guardar ") { wc_run(5,line[13..]); return true; }
    if wc_prefix(line,"guarda el documento como ") { wc_run(5,line[25..]); return true; }
    if wc_prefix(line,"/carpeta ") { wc_run(3,line[9..]); return true; }
    if wc_prefix(line,"crea una carpeta llamada ") { wc_run(3,line[25..]); return true; }
    if wc_exact(line,"/carpeta") || wc_exact(line,"crea una carpeta") { wc_run(3,wc_empty()); return true; }
    if wc_prefix(line,"/abrir ") { wc_run(14,line[7..]); return true; }
    if wc_prefix(line,"abre la carpeta ") { wc_run(14,line[16..]); return true; }
    if wc_prefix(line,"abre el documento ") { wc_run(14,line[18..]); return true; }
    if wc_prefix(line,"/proyecto nuevo ") { wc_run(4,line[16..]); return true; }
    if wc_prefix(line,"crea un proyecto llamado ") { wc_run(4,line[25..]); return true; }
    if wc_exact(line,"/proyecto nuevo") || wc_exact(line,"nuevo proyecto") { wc_run(4,wc_empty()); return true; }
    if wc_exact(line,"/general") || wc_exact(line,"/proyecto general") { wc_request(10,0,1,wc_empty()); wc_restore(); return true; }
    if wc_exact(line,"/inicio") { wc_request(7,0,0,wc_empty()); return true; }
    if wc_prefix(line,"/proyecto abrir ") {
        wc_request(0,0,0,wc_empty()); let count=wc_count; let name=line[16..];
        for id in 1..count+1 {
            wc_request(11,id,0,wc_empty()); let n=ai_get64(wc_reply[..],120);
            if ai_get64(wc_reply[..],112)!=3 || n!=name.len { continue; }
            var same=true; for i in 0..n { if wc_fold(wc_reply[128+i])!=wc_fold(name[i]) { same=false; } }
            if same { wc_request(10,id,0,wc_empty()); return true; }
        }
        wc_write("No encontre ese proyecto. Puedes crear uno con /proyecto nuevo Nombre.\n"); return true;
    }
    return false;
}
fn workspace_chat_route(line: []u8) -> bool {
    if wc_project!=0 { return false; }
    wc_request(0,0,0,wc_empty()); let count=wc_count; var candidate: u64=0;
    for id in 1..count+1 {
        wc_request(11,id,0,wc_empty()); let n=ai_get64(wc_reply[..],120);
        if ai_get64(wc_reply[..],112)!=3 || n<3 || n>line.len { continue; }
        for at in 0..line.len-n+1 {
            if at!=0 && line[at-1]!=32 { continue; }
            if at+n<line.len && line[at+n]!=32 && line[at+n]!=46 && line[at+n]!=44 && line[at+n]!=63 { continue; }
            var same=true; for i in 0..n { if wc_fold(line[at+i])!=wc_fold(wc_reply[128+i]) { same=false; } }
            if same { if candidate!=0 && candidate!=id { wc_write("Hay varios proyectos relacionados. Elige uno en Mis archivos.\n"); wc_run(1,wc_empty()); return true; } candidate=id; }
        }
    }
    if candidate==0 { return false; }
    wc_pending_n=line.len; wc_pending_project=candidate; for i in 0..line.len { wc_pending[i]=line[i]; }
    wc_request(10,candidate,0,wc_empty()); return true;
}
fn workspace_record(role: u64,text: []u8) {
    if text.len==0 || text.len>8192 { return; }
    if !wc_request(13,0,role,wc_empty()) { return; }
    var at: u64=0; while at<text.len { var end=at+184; if end>text.len { end=text.len; } if !wc_request(13,1,at,text[at..end]) { return; } at=end; }
    if !wc_request(13,2,0,wc_empty()) { wc_write("La conversacion sigue abierta, pero este mensaje no se pudo guardar.\n"); }
}
fn workspace_chat(line: []u8) {
    workspace_record(1,line); model_chat(line,64); workspace_record(2,model_chat_answer());
}
