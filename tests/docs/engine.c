#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "document.h"
#include "document_client.h"
#include "document_view.h"
#include "console.h"
#include "ai_wire.h"

static FILE *serial;
static erlan_su8 reply;
static u64 request_id, revision, draft_calls;
static u64 draft_status;
static unsigned renders;
static bool reject_view;
static u32 *pixels, *snapshot;
static const size_t width = 1024, height = 768, pitch = 1032;
void erlan_panic(const char *s) { fprintf(stderr,"%s\n",s); abort(); }
void kprintf(const char *s, ...) { (void)s; }
u64 ustrlen(const char *s) { return strlen(s); }
u64 ulog(const char *s) { fprintf(serial,"[system] %s\n",s); return 0; }
u64 ulog_bytes(erlan_su8 b) { fprintf(serial,"[system] %.*s\n",(int)b.len,b.ptr); return b.len; }
void ulog_num(const char *s, u64 n) { fprintf(serial,"[system] %s%llu\n",s,(unsigned long long)n); }
u64 fmt_str(erlan_su8 b,u64 at,const char *s) { size_t n=strlen(s); assert(at+n<=b.len); memcpy(b.ptr+at,s,n); return at+n; }
u64 fmt_u64(erlan_su8 b,u64 at,u64 n) { char text[32]; snprintf(text,sizeof(text),"%llu",(unsigned long long)n); return fmt_str(b,at,text); }
u64 usys(u64 nr,u64 a,u64 b,u64 c) {
    if(nr==18) { assert(a==3); ++renders; return !reject_view&&document_view((u8 *)(uintptr_t)b,c)?0:UINT64_MAX-1; }
    if(nr==8) return c;
    if(nr==13) { console_set_halo(b); return 0; }
    if(nr==17) return 0;
    abort();
}
u64 usend(u64 slot,erlan_su8 b) { assert(slot==8); reply=document_handle(b); return 0; }
u64 urecv(u64 slot,erlan_su8 b) { assert(slot==9&&b.len>=reply.len); memcpy(b.ptr,reply.ptr,reply.len); return reply.len; }
u64 model_chat_status(void) { return draft_status; }
erlan_su8 model_document_draft(erlan_su8 input) {
    ++draft_calls; assert(input.len>0);
    static u8 text[]="La energia solar permite aprovechar una fuente renovable.\nEste borrador sirve para verificar el editor nativo.";
    return (erlan_su8){text,sizeof(text)-1};
}
static u64 get(unsigned at) { return ai_get64(reply,at); }
static void call(u64 op,u64 a,u64 b,u64 c,u64 d,const char *s,u64 expected) {
    u8 bytes[256]={0}; size_t n=s?strlen(s):0; assert(n<=184);
    erlan_su8 req={bytes,72+n};
    ai_put64(req,0,0x31434f44); ai_put64(req,8,++request_id); ai_put64(req,16,op); ai_put64(req,24,revision);
    ai_put64(req,32,a); ai_put64(req,40,b); ai_put64(req,48,c); ai_put64(req,56,d); ai_put64(req,64,n);
    if(n) memcpy(bytes+72,s,n);
    reply=document_handle(req); assert(reply.len==256);
    if(get(24)!=expected) fprintf(stderr,"op=%llu request=%llu expected=%llu got=%llu revision=%llu\n",(unsigned long long)op,(unsigned long long)request_id,(unsigned long long)expected,(unsigned long long)get(24),(unsigned long long)get(40));
    assert(get(24)==expected); revision=get(40);
}
static void command(const char *s) { assert(document_command((erlan_su8){(u8*)s,strlen(s)})); }
int main(int argc,char **argv) {
    assert(argc==2); serial=fopen(argv[1],"wb"); assert(serial); setvbuf(serial,NULL,_IONBF,0);
    pixels=malloc(pitch*height*4); snapshot=malloc(pitch*height*4); assert(pixels&&snapshot);
    for(size_t i=0;i<pitch*height;++i) pixels[i]=0xfaceb00c;
    struct Fb f={pixels,width,height,pitch*4,16,8,0}; console_init(&f); document_init();
    call(0,0,0,0,0,NULL,0); assert(get(48)==210000&&get(56)==297000&&get(112)==110);
    call(1,0,0,0,0,NULL,0); call(2,0,0,0,0,"Documento de prueba",0);
    call(21,0,0,0,0,NULL,0); assert(get(176)==19&&memcmp(reply.ptr+184,"Documento de prueba",19)==0);
    call(3,0,0,0,0,"Primer parrafo con acentos: Espa\xc3\xb1ol.",0);
    call(4,20000,30000,25000,40000,NULL,0); call(7,1,0,0,0,NULL,0);
    assert(get(64)==20000&&get(72)==30000&&get(80)==25000&&get(88)==40000&&get(96)==1);
    memcpy(snapshot,pixels,pitch*height*4); u64 old=revision;
    call(4,20000,30000,UINT64_MAX,40000,NULL,5);
    assert(revision==old&&memcmp(snapshot,pixels,pitch*height*4)==0);
    call(6,2,110,0,0,NULL,5); call(6,0,0,0,0,NULL,5);
    call(6,0,111,0,0,NULL,5);
    call(3,0,0,0,0,"\xc0\xaf",5); assert(revision==old);
    reject_view=true; call(6,1,125,0,0,NULL,9); reject_view=false;
    assert(revision==old&&memcmp(snapshot,pixels,pitch*height*4)==0);
    call(17,0,0,0,0,"\fPagina segunda",0); assert(get(160)==2);
    call(9,2,0,0,0,NULL,0); assert(get(136)==2);
    call(8,0,0,0,0,NULL,0); assert(get(160)==1);
    // Stale revisions and replayed request IDs have no effect.
    u64 saved=revision; revision-=1; call(6,1,120,0,0,NULL,7); assert(revision==saved);
    request_id-=1; call(6,1,120,0,0,NULL,3); assert(revision==saved);
    call(12,0,0,0,0,NULL,0); call(13,1,0,0,0,"bad offset",7);
    call(13,0,0,0,0,"Nuevo contenido UTF-8: \xc3",0);
    call(13,999,0,0,0,"wrong",7);
    call(14,0,0,0,0,NULL,5); assert(revision==saved);
    // Mutation invalidates a pending text transaction without losing the old doc.
    call(12,0,0,0,0,NULL,0); call(13,0,0,0,0,"borrador",0);
    call(6,1,120,0,0,NULL,0); saved=revision; call(14,0,0,0,0,NULL,7); assert(revision==saved);
    // Reinitialize request IDs for the actual native language adapter.
    document_init();
    command("crea un documento"); command("/doc titulo Informe de energia solar");
    command("Pon arriba 2 cm, abajo 3, izquierda 2,5 y derecha 4");
    assert(get(64)==20000&&get(72)==30000&&get(80)==25000&&get(88)==40000);
    command("Usa margenes espejo"); assert(get(96)==1);
    command("Usa fuente sans 12"); assert(get(104)==1&&get(112)==120);
    saved=get(40);
    command("Pon arriba, abajo 3"); assert(get(40)==saved);
    command("Usa fuente serif -12"); assert(get(40)==saved);
    command("Pon margenes espejo arriba 2"); assert(get(40)==saved);
    command("Pon margenes 1 2 3 4 5"); assert(get(40)==saved);
    command("Pon margenes 2 -3"); assert(get(40)==saved);
    command("Pon arriba 2 metros"); assert(get(40)==saved);
    const char *question="explica la fuente de energia solar";
    assert(!document_command((erlan_su8){(u8*)question,strlen(question)}));
    command("/doc margenes 20 mm 30 mm 25 mm 40 mm");
    assert(get(64)==20000&&get(72)==30000&&get(80)==25000&&get(88)==40000);
    command("Redacta un informe sobre energia solar"); assert(draft_calls==1&&get(120)>40);
    saved=get(40); u64 saved_length=get(120);
    command("/doc cerrar"); draft_status=2;
    command("Redacta un informe distinto"); assert(get(40)==saved&&get(120)==saved_length);
    draft_status=0;
    command("/doc editar"); assert(document_editing());
    document_key('X'); document_key(8); document_key(10); document_key('A'); document_key('C');
    document_key(128); document_key('B'); document_key(131); document_key(27); assert(!document_editing());
    command("/doc salto"); command("/doc texto Segunda pagina del documento para comprobar margenes espejo.");
    command("/doc pagina 2"); assert(get(160)==2);
    command("/doc exportar");
    for(size_t y=0;y<height;++y) for(size_t x=width;x<pitch;++x) assert(pixels[y*pitch+x]==0xfaceb00c);
    assert(renders>10); fclose(serial); free(snapshot); free(pixels);
    puts("DOCS_HOST PASS native intents, units, mirrored pages, UTF-8, editing, undo, transactions, replay/revision rejection and framebuffer stride");
    return 0;
}
