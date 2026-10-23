// PS/2 mechanism. Interrupts only decode/enqueue; painting stays outside IRQs.
extern c "cpu.h" { fn inb(p: u16) -> u8; fn outb(p: u16,v: u8); }
extern c "pic.h" { fn pic_set_mask(irq: u8,masked: bool); }
extern c "keyboard.h" { fn keyboard_scancode(sc: u8); }
extern c "console.h" { fn console_framebuffer() -> ptr<Fb>; }
extern c "fb.h" { struct Fb { base: ptr<u32>; width: u64; height: u64; pitch: u64; rs: u8; gs: u8; bs: u8; }
    fn fb_rgb(f: ptr<Fb>,r: u8,g: u8,b: u8) -> u32; fn fb_pixel(f: ptr<Fb>,x: i64,y: i64,c: u32); }
extern c "serial.h" { fn kprintf(s: str,...); }
var pointer_x: i64=0; var pointer_y: i64=0; var pointer_buttons: u64=0;
var pointer_enabled: bool=false; var pointer_packet: [3]u8; var pointer_n: u64=0;
var pointer_queue: [128]u64; var pointer_head: u64=0; var pointer_tail: u64=0;
var pointer_under: [384]u32; var pointer_drawn: bool=false; var drawn_x: i64=0; var drawn_y: i64=0;
fn ps2_wait() -> bool { for i in 0..100000 { if (inb(0x64)&2)==0 { return true; } } return false; }
fn ps2_command(c: u8) -> bool { if !ps2_wait() { return false; } outb(0x64,c); return true; }
fn ps2_write(c: u8) -> bool { if !ps2_wait() { return false; } outb(0x60,c); return true; }
fn ps2_read() -> u64 { for i in 0..100000 { if (inb(0x64)&1)!=0 { return inb(0x60) as u64; } } return 256; }
fn mouse_command(c: u8) -> bool { if !ps2_command(0xd4) || !ps2_write(c) { return false; } return ps2_read()==250; }
fn pointer_init() {
    let f=console_framebuffer(); pointer_x=(f.width/2) as i64; pointer_y=(f.height/2) as i64;
    if !ps2_command(0xa8) || !ps2_command(0x20) { return; }
    let config=ps2_read(); if config>255 { return; }
    if !ps2_command(0x60) || !ps2_write(((config|2)&~(32 as u64)) as u8) || !mouse_command(0xf6) || !mouse_command(0xf4) { return; }
    pointer_enabled=true; pic_set_mask(2,false); pic_set_mask(12,false);
    kprintf("[pointer] PS/2 listo\n");
}
fn pointer_byte(c: u8) {
    if pointer_n==0 && (c&8)==0 { return; }
    pointer_packet[pointer_n]=c; pointer_n+=1; if pointer_n<3 { return; } pointer_n=0;
    let flags=pointer_packet[0]; if (flags&192)!=0 { return; }
    var dx=pointer_packet[1] as i64; var dy=pointer_packet[2] as i64;
    if (flags&16)!=0 { dx-=256; } if (flags&32)!=0 { dy-=256; }
    let f=console_framebuffer(); pointer_x+=dx; pointer_y-=dy;
    if pointer_x<0 { pointer_x=0; } if pointer_y<0 { pointer_y=0; }
    if pointer_x>=(f.width as i64) { pointer_x=(f.width as i64)-1; }
    if pointer_y>=(f.height as i64) { pointer_y=(f.height as i64)-1; }
    pointer_buttons=(flags&7) as u64;
    let event=(1<<40)|(pointer_buttons<<32)|((pointer_y as u64)<<16)|(pointer_x as u64);
    let next=(pointer_tail+1)%128;
    if next!=pointer_head { pointer_queue[pointer_tail]=event; pointer_tail=next; }
}
fn pointer_irq() {
    let status=inb(0x64); if (status&1)==0 { return; }
    let c=inb(0x60); if (status&32)!=0 { pointer_byte(c); } else { keyboard_scancode(c); }
}
fn pointer_get() -> u64 {
    if pointer_head==pointer_tail { return 0; } let e=pointer_queue[pointer_head]; pointer_head=(pointer_head+1)%128; return e;
}
fn pointer_position() -> u64 { return ((pointer_y as u64)<<16)|(pointer_x as u64); }
fn pointer_hide() {
    if !pointer_drawn { return; } let f=console_framebuffer();
    for y in 0..24 { for x in 0..16 { fb_pixel(f,drawn_x+(x as i64),drawn_y+(y as i64),pointer_under[y*16+x]); } }
    pointer_drawn=false;
}
fn pointer_show() {
    if !pointer_enabled || pointer_drawn { return; } let f=console_framebuffer(); drawn_x=pointer_x; drawn_y=pointer_y;
    for y in 0..24 { for x in 0..16 {
        let px=drawn_x+(x as i64); let py=drawn_y+(y as i64);
        if px<(f.width as i64) && py<(f.height as i64) { unsafe { let row=((f.base as ptr<u8>)+(py as u64)*f.pitch) as ptr<u32>; pointer_under[y*16+x]=row[px as u64]; } }
        var edge=false; var fill=false;
        if y<19 && x<=y/2 { fill=true; edge=x==0 || x==y/2 || y==18; }
        if y>=14 && y<24 && x>=5 && x<=8 { fill=true; edge=x==5 || x==8 || y==23; }
        if fill { var color=fb_rgb(f,246,236,221); if edge { color=fb_rgb(f,36,29,26); } fb_pixel(f,px,py,color); }
    } }
    pointer_drawn=true;
}
