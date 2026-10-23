#!/usr/bin/env python3
"""Compile and exercise actual Erlan journal, broker, document engine and UI."""
import os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'build/workspace-host';OUT.mkdir(parents=True,exist_ok=True)
(OUT/'usys.h').write_text('#include "erlan_rt.h"\nu64 usys(u64,u64,u64,u64);\n')
modules={'workspace_store':'services/system/workspace_store.e','workspace':'services/system/workspace.e',
         'document':'services/system/document.e','ai_wire':'services/lib/ai_wire.e',
         'workspace_view':'kernel/src/workspace_view.e','document_view':'kernel/src/document_view.e',
         'console':'kernel/src/console.e','fb':'kernel/src/fb.e'}
cc='C:/Program Files/LLVM/bin/clang.exe' if os.name=='nt' else 'clang';objects=[]
for name,source in modules.items():
    subprocess.run([sys.executable,'erlan/erlancompiler.py',source,'-o',str(OUT/(name+'.c')),'--header',str(OUT/(name+'.h'))],cwd=ROOT,check=True)
for name,source in modules.items():
    obj=OUT/(name+'.o');objects.append(str(obj))
    inc='kernel/src' if source.startswith('kernel/') else 'services/lib'
    subprocess.run([cc,'-O1','-g','-Wno-parentheses-equality',f'-I{OUT}',f'-I{inc}','-Ikernel/src','-Iservices/lib','-Ierlan/runtime','-Ibuild/ugen','-Ibuild/gen','-Iservices/lib','-Ikernel/src','-c',str(OUT/(name+'.c')),'-o',str(obj)],cwd=ROOT,check=True)
exe=OUT/('test.exe' if os.name=='nt' else 'test')
subprocess.run([cc,'-O1',f'-I{OUT}','-Ierlan/runtime','-Ibuild/ugen','-Ibuild/gen','-Iservices/lib','-Ikernel/src','tests/workspace/engine.c',*objects,'-o',str(exe)],cwd=ROOT,check=True)
subprocess.run([str(exe)],cwd=ROOT,check=True)
