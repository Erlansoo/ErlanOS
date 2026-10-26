// D1 append-only workspace journal. Payload + tail are flushed before the commit
// header. Existing records are never overwritten; an ambiguous failure seals the
// mounted store read-only. Ring 0 exposes only this partition, through slot 12.
import usys;
import ai_wire;
import document;
import ulib;
struct WsNode { parent: u64; kind: u64; length: u64; sector: u64; name_len: u64; name: [80]u8; }
var ws_nodes: [129]WsNode;
var ws_count: u64=0; var ws_tail: u64=8; var ws_sequence: u64=0;
var ws_capacity: u64=0; var ws_readonly: bool=false;
var ws_header: [512]u8; var ws_block: [512]u8;
var ws_error: u64=0;
fn ws_zero(b: []u8) { for i in 0..b.len { b[i]=0; } }
fn ws_copy(dst: []u8,src: []u8) { for i in 0..src.len { dst[i]=src[i]; } }
fn ws_sector(op: u64,sector: u64,b: []u8) -> bool {
    unsafe { return usys(21,12,&b[0] as u64,(sector<<2)|op)==0; }
}
fn ws_flush() -> bool { return usys(21,12,0,3)==0; }
fn ws_hash(hash: u64,b: []u8) -> u64 { var h=hash; for i in 0..b.len { h=(h^(b[i] as u64))*1099511628211; } return h; }
fn ws_fold(c: u8) -> u8 { if c>=65 && c<=90 { return c+32; } return c; }
fn ws_name_valid(name: []u8) -> bool {
    if name.len==0 || name.len>80 || !doc_text_valid(name,true) { return false; }
    if name[0]==32 || name[name.len-1]==32 || name[name.len-1]==46 { return false; }
    for i in 0..name.len { if name[i]==47 || name[i]==92 || name[i]==58 { return false; } }
    return true;
}
fn ws_same(a: []u8,b: []u8) -> bool {
    if a.len!=b.len { return false; }
    for i in 0..a.len { if ws_fold(a[i])!=ws_fold(b[i]) { return false; } } return true;
}
fn workspace_count() -> u64 { return ws_count; }
fn workspace_node(id: u64) -> ptr<WsNode> { return &ws_nodes[id]; }
fn workspace_available() -> u64 { if ws_capacity==0 { return 0; } if ws_readonly { return 1; } return 2; }
fn workspace_error() -> u64 { return ws_error; }
fn workspace_clear_error() { ws_error=0; }
fn workspace_find(parent: u64,name: []u8) -> u64 {
    for i in 1..ws_count+1 { let n=&ws_nodes[i]; if n.parent==parent && ws_same(name,n.name[0..n.name_len]) { return i; } } return 0;
}
fn ws_validate(id: u64,parent: u64,kind: u64,name: []u8,length: u64) -> bool {
    if id==0 || id>128 || id>ws_count+1 || parent>ws_count || !ws_name_valid(name) || kind==0 || kind>4 || length>32768 { return false; }
    if parent!=0 && ws_nodes[parent].kind!=1 && ws_nodes[parent].kind!=3 { return false; }
    if (kind==1 || kind==3) && length!=0 { return false; }
    if kind==2 && (length<224 || length>16608) { return false; }
    if kind==4 && parent!=0 && ws_nodes[parent].kind!=3 { return false; }
    if id<=ws_count && (ws_nodes[id].parent!=parent || ws_nodes[id].kind!=kind) { return false; }
    let duplicate=workspace_find(parent,name); if duplicate!=0 && duplicate!=id { return false; }
    return parent<id; // creation order forbids cycles; moving is a separate future operation.
}
fn ws_accept(h: []u8,sector: u64) {
    let id=ai_get64(h,24); let n=&mut ws_nodes[id];
    n.parent=ai_get64(h,32); n.kind=ai_get64(h,40); n.length=ai_get64(h,48); n.sector=sector+1;
    n.name_len=ai_get64(h,56); ws_copy(n.name[..],h[64..64+n.name_len]);
    if id>ws_count { ws_count=id; } ws_sequence=ai_get64(h,16);
}
fn workspace_mount() {
    ws_count=0; ws_tail=8; ws_sequence=0; ws_readonly=false; ws_error=0;
    ws_capacity=usys(21,12,0,0); if ws_capacity!=262144 { ws_capacity=0; ulog("workspace: sin almacenamiento"); return; }
    while ws_tail<ws_capacity {
        if !ws_sector(1,ws_tail,ws_header[..]) { ws_readonly=true; break; }
        let h=ws_header[..]; var empty=true; for i in 0..512 { if h[i]!=0 { empty=false; } }
        if empty { break; }
        let length=ai_get64(h,48); let name_len=ai_get64(h,56); let blocks=(length+511)/512;
        if ai_get64(h,0)!=0x314c4e4a53574c45 || ai_get64(h,8)!=1 || ai_get64(h,16)!=ws_sequence+1 || name_len>80 || length>32768 || ws_tail+1+blocks>=ws_capacity {
            ws_readonly=true; break;
        }
        if !ws_validate(ai_get64(h,24),ai_get64(h,32),ai_get64(h,40),h[64..64+name_len],length) { ws_readonly=true; break; }
        var hash=ws_hash(14695981039346656037,h[0..504]);
        var ok=true;
        for i in 0..blocks {
            if !ws_sector(1,ws_tail+1+i,ws_block[..]) { ok=false; break; }
            hash=ws_hash(hash,ws_block[..]);
        }
        if !ok || hash!=ai_get64(h,504) { ws_readonly=true; break; }
        ws_accept(h,ws_tail); ws_tail+=1+blocks;
    }
    ulog_num("workspace: objetos=",ws_count);
    if ws_readonly { ulog("workspace: cola interrumpida, conservado en solo lectura"); }
}
// id=0 creates, otherwise commits a new revision of an existing node.
fn workspace_put(id: u64,parent: u64,kind: u64,name: []u8,data: []u8) -> u64 {
    ws_error=0; if ws_capacity==0 || ws_readonly { ws_error=1; return 0; }
    var target=id; if target==0 { target=ws_count+1; }
    if target>128 { ws_error=4; return 0; }
    let duplicate=workspace_find(parent,name); if duplicate!=0 && duplicate!=id { ws_error=3; return 0; }
    if !ws_validate(target,parent,kind,name,data.len) { ws_error=2; return 0; }
    let blocks=(data.len+511)/512;
    if ws_tail+blocks+1>=ws_capacity { ws_error=4; return 0; }
    ws_zero(ws_header[..]); let h=ws_header[..];
    ai_put64(h,0,0x314c4e4a53574c45); ai_put64(h,8,1); ai_put64(h,16,ws_sequence+1);
    ai_put64(h,24,target); ai_put64(h,32,parent); ai_put64(h,40,kind); ai_put64(h,48,data.len); ai_put64(h,56,name.len); ws_copy(h[64..144],name);
    var hash=ws_hash(14695981039346656037,h[0..504]);
    for i in 0..blocks {
        ws_zero(ws_block[..]); let at=i*512; var end=at+512; if end>data.len { end=data.len; }
        ws_copy(ws_block[..],data[at..end]); hash=ws_hash(hash,ws_block[..]);
        if !ws_sector(2,ws_tail+1+i,ws_block[..]) { ws_error=5; ws_readonly=true; return 0; }
    }
    ws_zero(ws_block[..]);
    if !ws_sector(2,ws_tail+1+blocks,ws_block[..]) || !ws_flush() { ws_error=5; ws_readonly=true; return 0; }
    ai_put64(h,504,hash);
    if !ws_sector(2,ws_tail,h) || !ws_flush() { ws_error=5; ws_readonly=true; return 0; }
    ws_accept(h,ws_tail); ws_tail+=1+blocks;
    ulog_num("workspace: commit=",ws_sequence); return target;
}
fn workspace_read(id: u64,out: []u8) -> u64 {
    ws_error=0; if id==0 || id>ws_count { ws_error=6; return 0; }
    let n=&mut ws_nodes[id]; if n.length>out.len { ws_error=4; return 0; }
    if !ws_sector(1,n.sector-1,ws_header[..]) { ws_error=5; return 0; }
    let h=ws_header[..];
    if ai_get64(h,24)!=id || ai_get64(h,48)!=n.length || ai_get64(h,32)!=n.parent || ai_get64(h,40)!=n.kind { ws_error=5; return 0; }
    var hash=ws_hash(14695981039346656037,h[0..504]);
    let blocks=(n.length+511)/512;
    for i in 0..blocks {
        if !ws_sector(1,n.sector+i,ws_block[..]) { ws_error=5; return 0; }
        hash=ws_hash(hash,ws_block[..]);
        let at=i*512; var end=at+512; if end>n.length { end=n.length; }
        ws_copy(out[at..end],ws_block[0..end-at]);
    }
    if hash!=ai_get64(h,504) { ws_error=5; return 0; }
    return n.length;
}
