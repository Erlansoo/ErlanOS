#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include "workspace_store.h"
#include "workspace.h"
#include "document.h"
#include "ai_wire.h"
#include "fb.h"
#include "console.h"
#include "workspace_view.h"
#include "document_view.h"
static unsigned char disk[2*1024*1024],base[sizeof disk];
static unsigned checks;static int fail=-1,mutations,deny_view;static u64 request_id,doc_id;
static struct Fb fb;static u32 pixels[1280*800+64];
static erlan_su8 sl(void*p,u64 n){return (erlan_su8){p,n};}
static erlan_su8 text(const char*s){return sl((void*)s,strlen(s));}
#define CHECK(x) do{checks++;if(!(x)){fprintf(stderr,"FAIL %u line %d: %s\n",checks,__LINE__,#x);exit(1);}}while(0)
void erlan_panic(const char*s){fprintf(stderr,"PANIC %s\n",s);exit(2);}
void kprintf(const char*s,...){(void)s;}
u64 ulog(const char*s){(void)s;return 0;}u64 ulog_bytes(erlan_su8 b){return b.len;}void ulog_num(const char*s,u64 n){(void)s;(void)n;}
void pointer_hide(void){}void pointer_show(void){}u64 pointer_position(void){return 0;}
u64 usys(u64 nr,u64 a,u64 b,u64 c){
 if(nr==21){CHECK(a==12);u64 op=c&3,at=(c>>2)*512;
  if(!op)return 262144;
  if(op==3){if(mutations++==fail)return -2;return 0;}
  CHECK(at+512<=sizeof disk);CHECK(op==1||op==2);
  if(op==2){CHECK(at>=4096);if(mutations++==fail)return -2;memcpy(disk+at,(void*)b,512);}
  else memcpy((void*)b,disk+at,512);return 0;
 }
 if(nr==23){if(!b&&!c)return ((u64)800<<32)|1280;return workspace_view((void*)b,b?c:0)?0:-2;}
 if(nr==18){if(deny_view)return -2;return document_view((void*)b,c)?0:-2;}
 return 0;
}
static erlan_su8 doc(u64 op,u64 a,u64 b,u64 c,u64 d,const char*s){
 u8 r[256]={0};size_t n=s?strlen(s):0;CHECK(n<=184);
 ai_put64(sl(r,256),0,0x31434f44);ai_put64(sl(r,256),8,++doc_id);ai_put64(sl(r,256),16,op);
 ai_put64(sl(r,256),24,ai_get64(document_snapshot(),8));ai_put64(sl(r,256),32,a);ai_put64(sl(r,256),40,b);ai_put64(sl(r,256),48,c);ai_put64(sl(r,256),56,d);
 ai_put64(sl(r,256),64,n);if(n)memcpy(r+72,s,n);return document_handle(sl(r,72+n));
}
static erlan_su8 call(u64 op,u64 a,u64 b,const char*s){
 u8 r[256]={0};size_t n=s?strlen(s):0;CHECK(n<=184);
 ai_put64(sl(r,256),0,0x31505357);ai_put64(sl(r,256),8,++request_id);ai_put64(sl(r,256),16,op);ai_put64(sl(r,256),32,a);ai_put64(sl(r,256),40,b);ai_put64(sl(r,256),64,n);
 if(n)memcpy(r+72,s,n);return workspace_handle(sl(r,72+n));
}
static u64 status(erlan_su8 r){return ai_get64(r,24);}
int main(void){
 fb_init(&fb,pixels,1280,800,1280*4,16,8,0);console_init(&fb);document_init();workspace_init();
 CHECK(workspace_count()==0&&workspace_available()==2);
 CHECK(status(call(1,0,0,NULL))==0);CHECK(workspace_hit(1240,770)==1);CHECK(workspace_hit(675,280)==10);
 CHECK(status(call(3,0,0,"Documentos"))==0);CHECK(workspace_count()==1);
 CHECK(status(call(3,0,0,"documentos"))==3);CHECK(workspace_count()==1);
 CHECK(status(call(3,0,0,"../escape"))==2);CHECK(status(call(3,0,0,".."))==2);
 CHECK(status(call(7,1,0,NULL))==0);
 CHECK(status(doc(1,0,0,0,0,NULL))==0);CHECK(status(doc(2,0,0,0,0,"Informe nativo"))==0);
 CHECK(status(doc(3,0,0,0,0,"Espa\xc3\xb1ol, UTF-8 y geometria exacta."))==0);
 CHECK(status(doc(4,20000,30000,25000,40000,NULL))==0);
 CHECK(status(call(5,0,0,"Informe"))==0);CHECK(workspace_count()==2);
 u8 saved[16608];u64 len=document_snapshot().len;memcpy(saved,document_snapshot().ptr,len);
 workspace_mount();CHECK(workspace_count()==2);u8 loaded[16608];CHECK(workspace_read(2,sl(loaded,sizeof loaded))==len);CHECK(!memcmp(saved,loaded,len));
 CHECK(status(doc(3,0,0,0,0,"Cambios sin guardar"))==0);
 CHECK(status(call(6,2,0,NULL))==7);CHECK(ai_get64(call(0,0,0,NULL),48)==4);
 CHECK(status(call(8,31,0,NULL))==0);CHECK(ai_get64(document_snapshot(),88)>ai_get64(sl(saved,len),88));
 CHECK(status(call(5,0,0,NULL))==0);CHECK(workspace_count()==2);
 CHECK(status(call(6,2,0,NULL))==0);CHECK(ai_get64(document_snapshot(),32)==20000);
 // Import validation must leave current state intact on malformed UTF-8/scalars.
 u64 revision=ai_get64(document_snapshot(),8);loaded[224]=0xc0;
 CHECK(!document_restore(sl(loaded,len)));CHECK(ai_get64(document_snapshot(),8)==revision);
 loaded[224]=saved[224];ai_put64(sl(loaded,len),88,999999);
 CHECK(!document_restore(sl(loaded,len)));CHECK(ai_get64(document_snapshot(),8)==revision);
 deny_view=1;CHECK(!document_restore(sl(saved,len)));deny_view=0;CHECK(ai_get64(document_snapshot(),8)==revision);
 // Project creation proposes a switch; no implicit change. Cancel stays general.
 CHECK(status(call(7,0,0,NULL))==0);CHECK(status(call(4,0,0,"Energia Solar"))==0);
 CHECK(ai_get64(call(0,0,0,NULL),64)==0);CHECK(ai_get64(call(0,0,0,NULL),48)==5);
 call(8,31,0,NULL);CHECK(ai_get64(call(0,0,0,NULL),64)==0);
 call(10,3,0,NULL);call(8,30,0,NULL);CHECK(ai_get64(call(0,0,0,NULL),64)==3);
 CHECK(status(call(13,0,1,NULL))==0);CHECK(status(call(13,1,0,"Necesito energia solar"))==0);CHECK(status(call(13,2,0,NULL))==0);
 CHECK(ai_get64(call(12,0,0,NULL),112)==38);
 call(10,0,1,NULL);CHECK(ai_get64(call(12,0,0,NULL),112)==0);
 workspace_mount();call(10,3,1,NULL);CHECK(ai_get64(call(12,0,0,NULL),112)==38);
 // Fail every mutation point of a new document revision. Before commit, old
 // content survives remount; after a failed final flush, old or new is valid.
 memcpy(base,disk,sizeof disk);u8 original[16608];u64 oldlen=workspace_read(2,sl(original,sizeof original));
 for(int failure=0;failure<8;failure++){
  memcpy(disk,base,sizeof disk);fail=-1;workspace_mount();mutations=0;fail=failure;
  u8 next[1800];memset(next,'N',sizeof next);
  u64 id=workspace_put(2,1,2,text("Informe"),sl(next,sizeof next));CHECK(id==0);
  fail=-1;workspace_mount();u8 actual[16608];u64 actual_len=workspace_read(2,sl(actual,sizeof actual));
  CHECK((actual_len==oldlen&&!memcmp(actual,original,oldlen))||(failure==7&&actual_len==sizeof next&&!memcmp(actual,next,sizeof next)));
 }
 // Torn commit header: retain valid prefix and prohibit further writes.
 memcpy(disk,base,sizeof disk);workspace_mount();u64 tail=8;
 while(disk[tail*512])tail+=1+(ai_get64(sl(disk+tail*512,512),48)+511)/512;
 memset(disk+tail*512,0x7f,97);workspace_mount();CHECK(workspace_available()==1);CHECK(workspace_count()==4);
 CHECK(workspace_put(0,0,1,text("No"),sl(NULL,0))==0);
 for(int i=0;i<64;i++)CHECK(pixels[1280*800+i]==0);
 printf("PASS D1: %u checks, including 8 interrupted commits, exact reopen, project separation and framebuffer bounds\n",checks);return 0;
}
