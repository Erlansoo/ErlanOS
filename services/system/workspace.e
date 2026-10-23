// D1 application broker. Native documents, folders and project conversations.
extern c "workspace_store.h" {
    struct WsNode { parent: u64; kind: u64; length: u64; sector: u64; name_len: u64; name: [80]u8; }
    fn workspace_mount(); fn workspace_count() -> u64; fn workspace_node(id: u64) -> ptr<WsNode>;
    fn workspace_available() -> u64; fn workspace_error() -> u64;
    fn workspace_clear_error();
    fn workspace_find(parent: u64,name: []u8) -> u64;
    fn workspace_put(id: u64,parent: u64,kind: u64,name: []u8,data: []u8) -> u64;
    fn workspace_read(id: u64,out: []u8) -> u64;
    fn ws_copy(dst: []u8,src: []u8); fn ws_zero(b: []u8); fn ws_same(a: []u8,b: []u8) -> bool;
}
extern c "document.h" { fn document_snapshot() -> []u8; fn document_identity() -> u64; fn document_restore(b: []u8) -> bool; fn doc_text_valid(b: []u8,title: bool) -> bool; }
extern c "ai_wire.h" { fn ai_get64(b: []u8,p: u64) -> u64; fn ai_put64(b: []u8,p: u64,v: u64); }
extern c "usys.h" { fn usys(nr: u64,a: u64,b: u64,c: u64) -> u64; }
extern c "ulib.h" { fn ulog(s: str) -> u64; fn ulog_num(s: str,n: u64); }
var wx_visible: bool=false; var wx_folder: u64=0; var wx_project: u64=0; var wx_epoch: u64=0;
var wx_selected: u64=0; var wx_offset: u64=0; var wx_dialog: u64=0; var wx_target: u64=0;
var wx_name: [80]u8; var wx_name_n: u64=0; var wx_status: u64=0;
var wx_rows: [10]u64; var wx_rows_n: u64=0; var wx_total: u64=0;
var wx_document: u64=0; var wx_identity: u64=0; var wx_saved: u64=1; var wx_open_event: u64=0;
var wx_last: u64=0; var wx_reply: [256]u8; var wx_packet: [2048]u8;
var wx_data: [32768]u8;
var wx_chat: [32768]u8; var wx_chat_n: u64=0; var wx_chat_role: u64=0; var wx_chat_active: bool=false;
fn wx_str(b: []u8,s: str) -> u64 { var n: u64=0; unsafe { while s[n]!=0 && n<b.len { b[n]=s[n]; n+=1; } } return n; }
fn workspace_init() { workspace_mount(); wx_visible=false; wx_project=0; wx_epoch=0; wx_saved=ai_get64(document_snapshot(),8); wx_identity=document_identity(); }
fn wx_dirty() -> bool { return ai_get64(document_snapshot(),8)!=wx_saved; }
fn wx_message(status: u64) -> str {
    if status==1 { return "No hay almacenamiento disponible para escribir."; }
    if status==2 { return "Nombre o contenido invalido."; }
    if status==3 { return "Ya existe un elemento con ese nombre."; }
    if status==4 { return "No queda espacio. Tu trabajo anterior se conserva."; }
    if status==5 { return "No se pudo confirmar el guardado."; }
    if status==6 { return "No se encontro el elemento."; }
    if status==7 { return "Guarda los cambios antes de abrir otro documento."; }
    if status==8 { return "Documento invalido; el actual se conserva."; }
    if status==9 { return "La vista no esta disponible."; }
    if status==10 { return "Solicitud antigua o incompleta."; }
    return "Listo";
}
fn wx_close() { usys(23,3,0,1); wx_visible=false; wx_dialog=0; }
fn wx_list() {
    wx_rows_n=0; wx_total=0;
    let geometry=usys(23,3,0,0); var h=(geometry>>32)-136; if h>550 { h=550; }
    let maxrows=(h-212)/48;
    for group in 0..2 { for id in 1..workspace_count()+1 {
        let n=workspace_node(id); if n.parent!=wx_folder || n.kind==4 { continue; }
        if (group==0 && n.kind==2) || (group==1 && n.kind!=2) { continue; }
        if wx_total>=wx_offset && wx_rows_n<maxrows && wx_rows_n<10 { wx_rows[wx_rows_n]=id; wx_rows_n+=1; }
        wx_total+=1;
    } }
}
fn wx_render() {
    if wx_visible { wx_list(); } else { wx_rows_n=0; } ws_zero(wx_packet[..]); let p=wx_packet[..];
    ai_put64(p,0,0x31505357); if wx_visible { ai_put64(p,8,1); } ai_put64(p,16,wx_folder); ai_put64(p,24,wx_project);
    ai_put64(p,32,wx_selected); ai_put64(p,40,workspace_available()); ai_put64(p,48,wx_dialog); ai_put64(p,56,wx_status);
    ai_put64(p,64,wx_rows_n); ai_put64(p,72,wx_offset); ai_put64(p,80,wx_total); if wx_dirty() { ai_put64(p,88,1); }
    if wx_folder!=0 { let n=workspace_node(wx_folder); ws_copy(p[96..176],n.name[0..n.name_len]); }
    if wx_project!=0 { let n=workspace_node(wx_project); ws_copy(p[176..256],n.name[0..n.name_len]); }
    ws_copy(p[256..336],wx_name[0..wx_name_n]); wx_str(p[336..416],wx_message(wx_status));
    for i in 0..wx_rows_n {
        let id=wx_rows[i]; let n=workspace_node(id); let at=448+i*128;
        ai_put64(p,at,id); ai_put64(p,at+8,n.kind); ai_put64(p,at+16,n.length); ws_copy(p[at+24..at+104],n.name[0..n.name_len]);
    }
    unsafe { if usys(23,3,&wx_packet[0] as u64,448+wx_rows_n*128)!=0 { wx_status=9; } }
}
fn wx_prompt(dialog: u64) { wx_dialog=dialog; wx_visible=true; wx_name_n=0; wx_status=0; }
fn wx_save(name: []u8) -> bool {
    let b=document_snapshot(); var id=wx_document; var parent=wx_folder;
    if wx_identity!=document_identity() { id=0; }
    var filename=name;
    if id!=0 && name.len==0 { let n=workspace_node(id); parent=n.parent; filename=n.name[0..n.name_len]; }
    if filename.len==0 {
        wx_prompt(3); var len=ai_get64(b,112); if len>80 { len=80; }
        while len>0 && len<ai_get64(b,112) && (b[128+len]&192)==128 { len-=1; }
        ws_copy(wx_name[..],b[128..128+len]); wx_name_n=len; return false;
    }
    if name.len!=0 { id=0; }
    let saved=workspace_put(id,parent,2,filename,b);
    if saved==0 { wx_status=workspace_error(); return false; }
    wx_document=saved; wx_identity=document_identity(); wx_saved=ai_get64(b,8); wx_selected=saved; wx_dialog=0; wx_status=0;
    ulog_num("workspace: documento guardado id=",saved); return true;
}
fn wx_switch(id: u64,confirmed: bool) {
    if id!=0 && (id>workspace_count() || workspace_node(id).kind!=3) { wx_status=6; return; }
    if id==wx_project { return; }
    if !confirmed {
        wx_prompt(5); wx_target=id;
        if id!=0 { let n=workspace_node(id); ws_copy(wx_name[..],n.name[0..n.name_len]); wx_name_n=n.name_len; }
        else { wx_name_n=wx_str(wx_name[..],"Chat general"); }
        return;
    }
    wx_project=id; wx_epoch+=1; wx_chat_active=false; wx_folder=id; wx_offset=0; wx_selected=0; wx_close();
    ulog_num("workspace: chat activo=",id);
}
fn wx_open(id: u64,discard: bool) {
    if id==0 { wx_folder=0; wx_offset=0; wx_selected=0; return; }
    if id>workspace_count() { wx_status=6; return; } let n=workspace_node(id);
    if n.kind==1 || n.kind==3 {
        wx_folder=id; wx_offset=0; wx_selected=0;
        if n.kind==3 { wx_switch(id,false); } return;
    }
    if n.kind!=2 { wx_status=6; return; }
    if wx_dirty() && !discard { wx_prompt(4); wx_target=id; wx_status=7; return; }
    let length=workspace_read(id,wx_data[..]); if workspace_error()!=0 { wx_status=workspace_error(); return; }
    let was_visible=wx_visible; wx_close();
    if !document_restore(wx_data[0..length]) { wx_status=8; wx_visible=was_visible; return; }
    wx_document=id; wx_identity=document_identity(); wx_saved=ai_get64(document_snapshot(),8); wx_open_event=wx_saved;
    ulog_num("workspace: documento abierto id=",id);
}
fn wx_create(kind: u64,name: []u8) {
    if name.len==0 { wx_prompt(1); if kind==3 { wx_dialog=2; } return; }
    let id=workspace_put(0,wx_folder,kind,name,wx_data[0..0]);
    if id==0 { wx_status=workspace_error(); return; }
    wx_selected=id; wx_dialog=0;
    if kind==3 { wx_switch(id,false); }
}
fn wx_click(action: u64,is_double: bool) {
    wx_status=0;
    if action==1 { if wx_visible { wx_close(); } else { wx_visible=true; } return; }
    if !wx_visible { return; }
    if action==2 { wx_close(); }
    else if action==3 { if wx_folder!=0 { wx_folder=workspace_node(wx_folder).parent; wx_offset=0; wx_selected=0; } }
    else if action==4 { wx_folder=0; wx_offset=0; wx_selected=0; }
    else if action==10 { wx_prompt(1); }
    else if action==11 { wx_prompt(2); }
    else if action==12 { wx_save(wx_data[0..0]); }
    else if action==13 { wx_switch(0,true); }
    else if action==20 { if wx_offset>=wx_rows_n { wx_offset-=wx_rows_n; } else { wx_offset=0; } }
    else if action==21 { if wx_offset+wx_rows_n<wx_total { wx_offset+=wx_rows_n; } }
    else if action==31 { wx_dialog=0; }
    else if action==32 && wx_dialog==4 { wx_open(wx_target,true); }
    else if action==30 {
        let dialog=wx_dialog;
        if dialog==1 { wx_create(1,wx_name[0..wx_name_n]); }
        if dialog==2 { wx_create(3,wx_name[0..wx_name_n]); }
        if dialog==3 { wx_save(wx_name[0..wx_name_n]); }
        if dialog==4 { wx_save(wx_data[0..0]); }
        if dialog==5 { wx_switch(wx_target,true); }
    } else if action>=100 && action-100<wx_rows_n {
        wx_selected=wx_rows[action-100]; if is_double { wx_open(wx_selected,false); }
    }
}
fn wx_key(c: u64) {
    if c==27 { if wx_dialog!=0 { wx_dialog=0; } else { wx_close(); } return; }
    if wx_dialog>=1 && wx_dialog<=3 {
        if c==10 { if wx_name_n==0 { wx_status=2; } else { wx_click(30,false); } }
        else if c==8 && wx_name_n>0 { wx_name_n-=1; while wx_name_n>0 && (wx_name[wx_name_n]&192)==128 { wx_name_n-=1; } }
        else if c>=32 && c<127 && wx_name_n<80 { wx_name[wx_name_n]=c as u8; wx_name_n+=1; }
        return;
    }
    if c==10 { if wx_dialog!=0 { wx_click(30,false); } else if wx_selected!=0 { wx_open(wx_selected,false); } }
    if c==110 { wx_prompt(1); } if c==112 { wx_prompt(2); } if c==115 { wx_save(wx_data[0..0]); }
}
fn wx_chat_id() -> u64 { var name: [16]u8; let len=wx_str(name[..],"Conversacion"); return workspace_find(wx_project,name[0..len]); }
fn wx_chat_load() -> u64 { workspace_clear_error(); let id=wx_chat_id(); if id==0 { return 0; } return workspace_read(id,wx_data[..]); }
fn wx_chat_commit() {
    if !wx_chat_active || !doc_text_valid(wx_chat[0..wx_chat_n],false) { wx_status=2; return; }
    let old=wx_chat_load(); if workspace_error()!=0 { wx_status=workspace_error(); return; }
    let record=16+wx_chat_n; var start: u64=0;
    // Keep complete recent messages under 32 KiB, never split a UTF-8 character.
    while old-start+record>32768 && start<old { if start+16>old { wx_status=8; return; } start+=16+ai_get64(wx_data[..],start+8); if start>old { wx_status=8; return; } }
    var n=old-start; for i in 0..n { wx_data[i]=wx_data[start+i]; }
    ai_put64(wx_data[..],n,wx_chat_role); ai_put64(wx_data[..],n+8,wx_chat_n); n+=16;
    ws_copy(wx_data[n..],wx_chat[0..wx_chat_n]); n+=wx_chat_n;
    var name: [16]u8; let len=wx_str(name[..],"Conversacion");
    if workspace_put(wx_chat_id(),wx_project,4,name[0..len],wx_data[0..n])==0 { wx_status=workspace_error(); }
    wx_chat_active=false;
}
fn workspace_handle(req: []u8) -> []u8 {
    wx_status=0; wx_open_event=0; var id: u64=0; var op: u64=0; var payload_n: u64=0;
    ws_zero(wx_reply[..]);
    if req.len<72 || req.len>256 { wx_status=10; }
    else {
        id=ai_get64(req,8); op=ai_get64(req,16); let a=ai_get64(req,32); let b=ai_get64(req,40); let data=req[72..];
        if id==0 || id<=wx_last || ai_get64(req,0)!=0x31505357 || ai_get64(req,64)!=data.len { wx_status=10; }
        else {
            wx_last=id;
            if op==0 { }
            else if op==1 { wx_visible=true; }
            else if op==2 { wx_close(); }
            else if op==3 { wx_create(1,data); }
            else if op==4 { wx_create(3,data); }
            else if op==5 { wx_save(data); }
            else if op==6 { wx_open(a,false); }
            else if op==7 { if a<=workspace_count() && (a==0 || workspace_node(a).kind==1 || workspace_node(a).kind==3) { wx_folder=a; wx_offset=0; wx_selected=0; } else { wx_status=6; } }
            else if op==8 { wx_click(a,b==1); }
            else if op==9 { if wx_visible { wx_key(a); } }
            else if op==10 { wx_switch(a,b==1); }
            else if op==11 {
                if a>workspace_count() { wx_status=6; }
                else if a!=0 { let n=workspace_node(a); ai_put64(wx_reply[..],104,n.parent); ai_put64(wx_reply[..],112,n.kind); payload_n=n.name_len; ws_copy(wx_reply[128..208],n.name[0..payload_n]); }
            } else if op==12 {
                let n=wx_chat_load(); if workspace_error()!=0 { wx_status=workspace_error(); }
                else if a>n { wx_status=2; }
                else { payload_n=n-a; if payload_n>128 { payload_n=128; } ws_copy(wx_reply[128..],wx_data[a..a+payload_n]); ai_put64(wx_reply[..],112,n); }
            } else if op==13 {
                if a==0 { wx_chat_n=0; wx_chat_role=b; wx_chat_active=b==1 || b==2; }
                else if a==1 && wx_chat_active && b==wx_chat_n && wx_chat_n+data.len<=8192 { ws_copy(wx_chat[wx_chat_n..],data); wx_chat_n+=data.len; }
                else if a==2 { wx_chat_commit(); }
                else { wx_status=2; wx_chat_active=false; }
            } else if op==14 {
                let found=workspace_find(wx_folder,data); if found==0 { wx_status=6; } else { wx_open(found,false); }
            } else { wx_status=2; }
            if op!=0 && op!=11 && op!=12 && op!=13 { wx_render(); }
        }
    }
    let r=wx_reply[..]; ai_put64(r,0,0x31505357); ai_put64(r,8,id); ai_put64(r,16,op); ai_put64(r,24,wx_status);
    if wx_visible { ai_put64(r,40,1); } ai_put64(r,48,wx_dialog); ai_put64(r,56,wx_folder); ai_put64(r,64,wx_project);
    ai_put64(r,72,wx_open_event); ai_put64(r,80,wx_selected); ai_put64(r,88,workspace_available()); ai_put64(r,96,workspace_count()); ai_put64(r,120,payload_n);
    ai_put64(r,32,wx_epoch);
    if wx_status!=0 { ulog_num("workspace: error=",wx_status); }
    return r;
}
