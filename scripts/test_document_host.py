#!/usr/bin/env python3
"""Execute the actual Erlan document engine, intent adapter and page renderer."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/docs-host'
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'usys.h').write_text('#ifndef DOC_HOST_USYS_H\n#define DOC_HOST_USYS_H\n#include "erlan_rt.h"\nu64 usys(u64,u64,u64,u64);\n#endif\n')
cc=shutil.which('clang') or 'C:/Program Files/LLVM/bin/clang.exe'
modules={'console':'kernel/src/console.e','fb':'kernel/src/fb.e','document_view':'kernel/src/document_view.e',
         'document':'services/system/document.e','document_client':'mind/document_client.e','ai_wire':'services/lib/ai_wire.e',
         'font':'kernel/src/font.e','docfont':'kernel/src/docfont.e'}
for name,source in modules.items():
    subprocess.run([sys.executable,'erlan/erlancompiler.py',source,'-o',str(OUT/(name+'.c')),'--header',str(OUT/(name+'.h')),'-Iservices/lib','-Iservices/model','-Imind','-Iservices/system','-Ikernel/src'],cwd=ROOT,check=True)
# Headers de todos los módulos del kernel (imports transitivos); sólo se enlazan los listados.
for header in sorted((ROOT/'kernel/src').glob('*.e')):
    if header.stem not in modules:
        subprocess.run([sys.executable,'erlan/erlancompiler.py',str(header),'-o',str(OUT/(header.stem+'.c')),'--header',str(OUT/(header.stem+'.h')),'-Ikernel/src','-Inet','-Inet/tls'],cwd=ROOT,check=True)
objects=[]
for name,source in modules.items():
    obj=OUT/(name+'.o'); objects.append(obj)
    specific='kernel/src' if source.startswith('kernel/') else 'services/lib'
    subprocess.run([cc,'-std=c11','-O1','-g','-Wno-parentheses-equality',f'-I{OUT}',f'-I{specific}',
        '-Iservices/lib','-Iservices/model','-Ibuild/ugen/model','-Ibuild/ugen','-Ikernel/src','-Ierlan/runtime','-Iassets/fonts',
        '-c',str(OUT/(name+'.c')),'-o',str(obj)],cwd=ROOT,check=True)
exe=OUT/('test.exe' if os.name=='nt' else 'test')
subprocess.run([cc,'-std=c11','-O1',f'-I{OUT}','-Ierlan/runtime','-Ibuild/ugen','-Ibuild/ugen/model','-Iservices/model','-Iservices/lib','-Ikernel/src','-Iassets/fonts',
                'tests/docs/engine.c',*map(str,objects),'-o',str(exe)],cwd=ROOT,check=True)
subprocess.run([str(exe),str(OUT/'serial.log')],cwd=ROOT,check=True)
