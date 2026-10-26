// PS/2 mechanism. Interrupts only decode/enqueue; painting stays outside IRQs.
import cpu;
import pic;
import apic;
import i8042;
import keyboard;
import console;
import fb;
import serial;
var pointer_x: i64=0; var pointer_y: i64=0; var pointer_buttons: u64=0;
var pointer_enabled: bool=false; var pointer_packet: [3]u8; var pointer_n: u64=0;
var pointer_queue: [128]u64; var pointer_head: u64=0; var pointer_tail: u64=0;
var pointer_under: [384]u32; var pointer_drawn: bool=false; var drawn_x: i64=0; var drawn_y: i64=0;
fn mouse_command(c: u8) -> bool { return i8042_aux_command(c)==250; }
// Ratón como lo hace psmouse en Linux: reinicio (0xFF -> 0xFA, 0xAA, id), valores por defecto
// (0xF6) y habilitar el flujo (0xF4), con plazo en cada paso; el reinicio no es obligatorio.
fn pointer_init() {
    let f=console_framebuffer(); pointer_x=(f.width/2) as i64; pointer_y=(f.height/2) as i64;
    if !i8042_aux_present() { kprintf("[pointer] sin puerto auxiliar PS/2\n"); return; }
    var reset_ok=false;
    if i8042_aux_command(0xff)==250 { let bat=i8042_aux_read(); let id=i8042_aux_read(); reset_ok = bat==0xAA && id==0; }
    if !mouse_command(0xf6) || !mouse_command(0xf4) { kprintf("[pointer] el raton no responde\n"); return; }
    if !i8042_enable_aux() { return; }
    pointer_enabled=true;
    if !apic_ioapic_mode() { pic_set_mask(2,false); pic_set_mask(12,false); }
    kprintf("[pointer] PS/2 listo (reinicio=%d, %s)\n", reset_ok as u64, "irq12");
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
