// Trusted D1 surface: bounded folder rows over the existing canvas. No paths,
// block addresses or filesystem policy enter the renderer.
extern c "fb.h" { struct Fb { base: ptr<u32>; width: u64; height: u64; pitch: u64; rs: u8; gs: u8; bs: u8; }
    fn fb_rgb(f: ptr<Fb>,r: u8,g: u8,b: u8) -> u32; fn fb_pixel(f: ptr<Fb>,x: i64,y: i64,c: u32); fn fb_rect(f: ptr<Fb>,x: i64,y: i64,w: i64,h: i64,c: u32); }
extern c "console.h" { fn console_framebuffer() -> ptr<Fb>; fn canvas_color(x: i64,y: i64) -> u32; }
extern c "document_view.h" { fn dv_decode(b: []u8,at: u64) -> u64; }
extern c "docfont.h" { fn docfont_advance(font: u64,ch: u64) -> u64; fn docfont_alpha(font: u64,ch: u64,x: u64,y: u64) -> u64; }
extern c "pointer.h" { fn pointer_position() -> u64; fn pointer_hide(); fn pointer_show(); }
var wv_f: ptr<Fb>; var wv_x: i64=0; var wv_y: i64=0; var wv_w: i64=0; var wv_h: i64=0;
var wv_open: bool=false; var wv_packet: [2048]u8; var wv_under: [400000]u32;
var wv_project: u64=0; var wv_project_name: [80]u8;
fn wv_get(b: []u8,p: u64) -> u64 { var n: u64=0; for i in 0..8 { n=n|((b[p+i] as u64)<<(i*8)); } return n; }
fn wv_rgb(r: u8,g: u8,b: u8) -> u32 { return fb_rgb(wv_f,r,g,b); }
fn wv_pixel(x: i64,y: i64) -> u32 { if x<0 || y<0 || x>=(wv_f.width as i64) || y>=(wv_f.height as i64) { return 0; } unsafe { let row=((wv_f.base as ptr<u8>)+(y as u64)*wv_f.pitch) as ptr<u32>; return row[x as u64]; } }
fn wv_mix(a: u32,b: u32,alpha: u64) -> u32 {
    let r=(((a>>(wv_f.rs as u32))&255) as u64)*(255-alpha)+(((b>>(wv_f.rs as u32))&255) as u64)*alpha;
    let g=(((a>>(wv_f.gs as u32))&255) as u64)*(255-alpha)+(((b>>(wv_f.gs as u32))&255) as u64)*alpha;
    let bl=(((a>>(wv_f.bs as u32))&255) as u64)*(255-alpha)+(((b>>(wv_f.bs as u32))&255) as u64)*alpha;
    return wv_rgb((r/255) as u8,(g/255) as u8,(bl/255) as u8);
}
fn wv_round(x: i64,y: i64,w: i64,h: i64,r: i64,color: u32) {
    for py in 0..h { for px in 0..w {
        var dx: i64=0; var dy: i64=0;
        if px<r { dx=r-px; } else if px>=w-r { dx=px-(w-r-1); }
        if py<r { dy=r-py; } else if py>=h-r { dy=py-(h-r-1); }
        let d=dx*dx+dy*dy;
        if d<=r*r { fb_pixel(wv_f,x+px,y+py,color); }
        else if d<(r+1)*(r+1) { fb_pixel(wv_f,x+px,y+py,wv_mix(wv_pixel(x+px,y+py),color,((r+1)*(r+1)-d) as u64*255/((2*r+1) as u64))); }
    } }
}
fn wv_text(x: i64,y: i64,bytes: []u8,size: u64,color: u32,maxw: i64) {
    var at: u64=0; var pen=x;
    while at<bytes.len && bytes[at]!=0 {
        let d=dv_decode(bytes,at); if d==0 { return; } let ch=d&0xffffffff;
        let advance=(docfont_advance(1,ch)*size/2048) as i64;
        if pen+advance>x+maxw { return; }
        let gw=(40*size+31)/32; let gh=(48*size+31)/32;
        for py in 0..gh { for px in 0..gw {
            let sx=px*32/size; let sy=py*32/size;
            let a=docfont_alpha(1,ch,sx,sy);
            let dx=pen+(px as i64)-((3*size/32) as i64); let dy=y+(py as i64)-((8*size/32) as i64);
            if a>0 { fb_pixel(wv_f,dx,dy,wv_mix(wv_pixel(dx,dy),color,a)); }
        } }
        pen+=advance; at+=d>>32;
    }
}
fn wv_label(x: i64,y: i64,s: str,size: u64,color: u32) {
    var b: [160]u8; var n: u64=0; unsafe { while s[n]!=0 && n<b.len { b[n]=s[n]; n+=1; } } wv_text(x,y,b[0..n],size,color,600);
}
fn wv_folder(x: i64,y: i64,size: i64,project: bool) {
    wv_round(x,y,size*3/5,size/3,3,wv_rgb(222,180,128));
    wv_round(x,y+size/6,size,size*2/3,4,wv_rgb(185,137,88));
    wv_round(x+1,y+size/4,size-2,size/2,3,wv_rgb(234,197,147));
    if project { fb_rect(wv_f,x+size/2-2,y+size/3,4,size/3,wv_rgb(111,73,49)); fb_rect(wv_f,x+size/3,y+size/2-1,size/3,3,wv_rgb(111,73,49)); }
}
fn workspace_geometry() -> u64 { let f=console_framebuffer(); return (f.height<<32)|f.width; }
fn wv_layout() {
    wv_f=console_framebuffer(); wv_w=(wv_f.width as i64)-48; if wv_w>660 { wv_w=660; }
    wv_h=(wv_f.height as i64)-136; if wv_h>550 { wv_h=550; }
    wv_x=(wv_f.width as i64)-24-wv_w; wv_y=(wv_f.height as i64)-92-wv_h;
}
fn wv_save(restore: bool) {
    for y in 0..wv_h+12 { for x in 0..wv_w+12 {
        let i=(y*(wv_w+12)+x) as u64;
        if restore { fb_pixel(wv_f,wv_x+x-4,wv_y+y-4,wv_under[i]); }
        else { wv_under[i]=wv_pixel(wv_x+x-4,wv_y+y-4); }
    } }
}
fn workspace_button() {
    wv_f=console_framebuffer(); let w=wv_f.width as i64; let h=wv_f.height as i64;
    if w<640 || h<480 { return; }
    let pos=pointer_position(); let dx=w-((pos&65535) as i64); let dy=h-((pos>>16) as i64); let hover=dx*dx+dy*dy<8464;
    for y in h-94..h { for x in w-94..w {
        let a=w-x; let b=h-y; let d=a*a+b*b;
        if d<=8836 {
            var c=wv_rgb((82+d/240) as u8,(58+d/360) as u8,(43+d/530) as u8);
            if hover || wv_open { c=wv_mix(c,wv_rgb(226,182,126),38); }
            if d>8281 { c=wv_mix(c,wv_rgb(217,171,119),((d-8281)*150/555) as u64); }
            if d>8649 { c=wv_mix(canvas_color(x,y),c,((8836-d)*255/187) as u64); }
            fb_pixel(wv_f,x,y,c);
        }
    } }
    wv_folder(w-55,h-46,30,false);
    if wv_project!=0 { wv_round(w-238,18,214,36,12,wv_rgb(53,42,33)); wv_folder(w-224,28,17,true); wv_text(w-197,29,wv_project_name[..],13,wv_rgb(232,208,176),160); }
}
fn wv_action(x: i64,y: i64,w: i64,s: str) {
    wv_round(x,y,w,34,9,wv_rgb(66,53,44)); wv_label(x+12,y+8,s,14,wv_rgb(238,214,184));
}
fn wv_paint() {
    let p=wv_packet[..]; let fg=wv_rgb(241,231,216); let dim=wv_rgb(167,151,135); let copper=wv_rgb(226,185,136);
    wv_round(wv_x+4,wv_y+7,wv_w,wv_h,22,wv_rgb(18,16,16));
    wv_round(wv_x,wv_y,wv_w,wv_h,20,wv_rgb(103,79,59));
    wv_round(wv_x+1,wv_y+1,wv_w-2,wv_h-2,19,wv_rgb(37,32,29));
    wv_folder(wv_x+25,wv_y+25,24,false); wv_label(wv_x+65,wv_y+23,"Mis archivos",23,fg);
    wv_label(wv_x+wv_w-38,wv_y+23,"x",20,dim);
    wv_label(wv_x+25,wv_y+69,"<",18,copper); wv_label(wv_x+54,wv_y+71,"Inicio",14,dim);
    if wv_get(p,16)!=0 { wv_label(wv_x+104,wv_y+70,"/",16,dim); wv_text(wv_x+123,wv_y+71,p[96..176],15,fg,wv_w-164); }
    wv_action(wv_x+24,wv_y+105,137,"+ Carpeta"); wv_action(wv_x+171,wv_y+105,137,"+ Proyecto");
    wv_action(wv_x+318,wv_y+105,119,"Guardar");
    if wv_w>590 { wv_action(wv_x+447,wv_y+105,wv_w-471,"Chat general"); }
    let count=wv_get(p,64); let dialog=wv_get(p,48);
    if count==0 && dialog==0 {
        let cx=wv_x+wv_w/2;
        if wv_h>=450 {
            wv_round(cx-38,wv_y+192,76,76,25,wv_rgb(56,43,34)); wv_folder(cx-20,wv_y+214,40,false);
            wv_label(cx-84,wv_y+288,"Un lugar para crear",19,fg);
            wv_label(cx-132,wv_y+322,"Tus carpetas y proyectos empiezan aqui.",14,dim);
        } else { wv_folder(cx-17,wv_y+178,34,false); wv_label(cx-84,wv_y+227,"Un lugar para crear",19,fg); wv_label(cx-132,wv_y+259,"Tus carpetas y proyectos empiezan aqui.",14,dim); }
    }
    var limit=count; if dialog!=0 { var available: u64=0; if wv_h>330 { available=((wv_h-330)/48) as u64; } if available>3 { available=3; } if limit>available { limit=available; } }
    for i in 0..limit {
        let at=448+i*128; let row_y=wv_y+155+(i as i64)*48; let kind=wv_get(p,at+8);
        if wv_get(p,at)==wv_get(p,32) { wv_round(wv_x+16,row_y,wv_w-32,45,10,wv_rgb(72,53,39)); }
        if kind==1 || kind==3 { wv_folder(wv_x+30,row_y+11,28,kind==3); }
        else { wv_round(wv_x+34,row_y+7,20,28,3,wv_rgb(230,218,199)); for j in 0..3 { fb_rect(wv_f,wv_x+38,row_y+14+(j as i64)*5,12,1,wv_rgb(151,127,102)); } }
        wv_text(wv_x+77,row_y+6,p[at+24..at+104],16,fg,wv_w-110);
        if kind==1 { wv_label(wv_x+77,row_y+28,"Carpeta",11,dim); }
        else if kind==3 { wv_label(wv_x+77,row_y+28,"Proyecto / conversacion propia",11,copper); }
        else { wv_label(wv_x+77,row_y+28,"Documento Erlan",11,dim); }
    }
    if dialog!=0 {
        wv_round(wv_x+16,wv_y+wv_h-175,wv_w-32,130,12,wv_rgb(51,41,34));
        var title: str="Nombre de la carpeta";
        if dialog==2 { title="Nombre del nuevo proyecto"; } if dialog==3 { title="Guardar documento como"; }
        if dialog==4 { title="Hay cambios sin guardar. Guardalos antes de abrir."; }
        if dialog==5 { title="Continuar en el chat de este proyecto?"; }
        wv_label(wv_x+29,wv_y+wv_h-161,title,15,fg);
        if dialog<=3 {
            wv_round(wv_x+28,wv_y+wv_h-132,wv_w-56,36,7,wv_rgb(29,26,24));
            wv_text(wv_x+39,wv_y+wv_h-123,p[256..336],17,fg,wv_w-90);
        } else if dialog==5 { wv_text(wv_x+30,wv_y+wv_h-127,p[256..336],18,copper,wv_w-60); }
        wv_action(wv_x+wv_w-136,wv_y+wv_h-87,108,"Aceptar");
        wv_action(wv_x+wv_w-256,wv_y+wv_h-87,108,"Cancelar");
        if dialog==4 { wv_action(wv_x+28,wv_y+wv_h-87,142,"Abrir sin guardar"); }
    }
    fb_rect(wv_f,wv_x+24,wv_y+wv_h-38,wv_w-48,1,wv_rgb(72,57,45));
    if wv_get(p,56)!=0 { wv_text(wv_x+26,wv_y+wv_h-26,p[336..416],12,wv_rgb(244,172,145),wv_w-110); }
    else if wv_get(p,40)!=2 { wv_label(wv_x+26,wv_y+wv_h-26,"Almacenamiento no disponible para guardar",12,dim); }
    else { wv_label(wv_x+26,wv_y+wv_h-26,"Doble clic para abrir",12,dim); }
    wv_label(wv_x+wv_w-83,wv_y+wv_h-27,"<    >",14,copper);
}
fn workspace_view(p: ptr<u8>,len: u64) -> bool {
    wv_layout(); if wv_f.width<640 || wv_f.height<480 { return false; }
    if len==0 { if wv_open { wv_save(true); } wv_open=false; return true; }
    if len<448 || len>1728 { return false; }
    var check: [2048]u8; unsafe { for i in 0..len { check[i]=p[i]; } }
    let b=check[0..len]; let count=wv_get(b,64);
    if wv_get(b,0)!=0x31505357 || wv_get(b,8)>1 || count>10 || len!=448+count*128 || wv_get(b,48)>5 { return false; }
    let maxrows=((wv_h-212)/48) as u64; if count>maxrows { return false; }
    // Strings are fixed size, bounded by NUL, validated before any pixels change.
    for j in 0..4+count {
        var at=96+j*80; if j>=4 { at=448+(j-4)*128+24; }
        var q: u64=0; while q<80 && b[at+q]!=0 { let d=dv_decode(b[at..at+80],q); if d==0 || (d&0xffffffff)<32 { return false; } q+=d>>32; }
    }
    if wv_project!=wv_get(b,24) {
        for py in 17..56 { for px in (wv_f.width as i64)-240..(wv_f.width as i64)-22 { fb_pixel(wv_f,px,py as i64,canvas_color(px,py as i64)); } }
    }
    wv_project=wv_get(b,24); for i in 0..80 { wv_project_name[i]=b[176+i]; }
    if wv_get(b,8)==0 { return true; }
    if !wv_open { wv_save(false); }
    for i in 0..len { wv_packet[i]=b[i]; } wv_open=true; wv_paint(); return true;
}
fn workspace_hit(x: u64,y: u64) -> u64 {
    wv_layout(); let px=x as i64; let py=y as i64; let dx=(wv_f.width as i64)-px; let dy=(wv_f.height as i64)-py;
    if dx*dx+dy*dy<8836 { return 1; }
    if !wv_open || px<wv_x || px>=wv_x+wv_w || py<wv_y || py>=wv_y+wv_h { return 0; }
    let rx=px-wv_x; let ry=py-wv_y;
    if rx>wv_w-54 && ry<58 { return 2; }
    if ry>=62 && ry<94 { if rx<50 { return 3; } return 4; }
    if ry>=105 && ry<139 { if rx<166 { return 10; } if rx<313 { return 11; } if rx<442 { return 12; } return 13; }
    let dialog=wv_get(wv_packet[..],48);
    if dialog!=0 && ry>=wv_h-87 && ry<wv_h-53 { if rx>wv_w-144 { return 30; } if rx>wv_w-268 { return 31; } if dialog==4 { return 32; } }
    if dialog!=0 && ry>=wv_h-175 { return 0; }
    if ry>=wv_h-38 { if rx>wv_w-50 { return 21; } if rx>wv_w-98 { return 20; } return 0; }
    if ry>=155 { let row=((ry-155)/48) as u64; if row<wv_get(wv_packet[..],64) && (dialog==0 || row<3) { return 100+row; } }
    return 0;
}
fn desktop_begin() { pointer_hide(); }
fn desktop_end() { workspace_button(); pointer_show(); }
