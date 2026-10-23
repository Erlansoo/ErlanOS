#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pointer.h"
static u32 pixels[641*480],copy[641*480];
static struct Fb f={pixels,640,480,641*4,16,8,0};
void erlan_panic(const char*s){fputs(s,stderr);abort();}
struct Fb *console_framebuffer(void){return &f;}
u8 inb(u16 p){static int n;u8 reply[]={0,250,250};if(p==0x64)return 1;return reply[n++];}
void outb(u16 p,u8 v){(void)p;(void)v;}
void pic_set_mask(u8 irq,bool masked){assert(!masked&&(irq==2||irq==12));}
void keyboard_scancode(u8 c){(void)c;}
void kprintf(const char*s,...){(void)s;}
static void packet(u8 flags,u8 x,u8 y){pointer_byte(flags);pointer_byte(x);pointer_byte(y);}
int main(void){
 for(int i=0;i<641*480;i++)pixels[i]=(u32)i;memcpy(copy,pixels,sizeof pixels);
 pointer_init();assert(pointer_position()==((u64)240<<16|320));
 pointer_byte(0);packet(8,10,20);u64 e=pointer_get();assert((e&65535)==330&&((e>>16)&65535)==220);
 packet(0x39,255,255);e=pointer_get();assert((e&65535)==329&&((e>>16)&65535)==221&&((e>>32)&7)==1);
 packet(8,0,0);e=pointer_get();assert(((e>>32)&7)==0);assert(pointer_get()==0);
 packet(0xc8,12,12);assert(pointer_get()==0);
 for(int i=0;i<5;i++)packet(0x18,0,255); // clamp negative X and top edge
 pointer_show();pointer_hide();assert(!memcmp(copy,pixels,sizeof pixels));
 for(int i=0;i<5;i++)packet(0x28,255,0); // clamp right and bottom edges
 assert(pointer_position()==((u64)479<<16|639));pointer_show();pointer_hide();assert(!memcmp(copy,pixels,sizeof pixels));
 puts("PASS PS/2: sync, signed motion, buttons, overflow, clipping, pitch and exact cursor restore");
}
