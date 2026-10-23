#ifndef MODEL_INPUT_FRONTEND_STUBS_H
#define MODEL_INPUT_FRONTEND_STUBS_H
#include "erlan_rt.h"
#ifndef ERLAN_DEF_erlan_su8
#define ERLAN_DEF_erlan_su8
ERLAN_SLICE(erlan_su8, u8)
#endif
u8 platform_getkey(void);
void platform_write(u8 *, u64);
void platform_log(const char *);
void platform_clear_cancel(void);
void platform_halo(u64);
void platform_sleep_ms(u64);
u64 platform_map_module(u64);
u64 platform_module_size(u64);
u64 platform_map_anon(u64);
u64 ulog(const char *);
void ulog_num(const char *, u64);
void uexit(u64);
bool model_load(u64, u64, u64, u64);
u64 model_arena_used(void);
bool model_warmup(void);
u64 model_chat_context(void);
bool mind_handle(erlan_su8);
void mind_help(void);
void model_plan_invalidate(void);
erlan_su8 gguf_model_name(void);
u64 model_chat(erlan_su8, u64);
bool document_command(erlan_su8);
bool document_editing(void);
void document_key(u8);
void workspace_client_init(void);
void workspace_poll(void);
bool workspace_key(u8);
bool workspace_command(erlan_su8);
bool workspace_chat_route(erlan_su8);
void workspace_chat(erlan_su8);
#endif
