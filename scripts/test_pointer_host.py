import os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];out=root/'build/pointer-host';out.mkdir(parents=True,exist_ok=True)
(out/'cpu.h').write_text('#include "erlan_rt.h"\nu8 inb(u16);void outb(u16,u8);\n')
cc='C:/Program Files/LLVM/bin/clang.exe' if os.name=='nt' else 'clang'
subprocess.run([sys.executable,'erlan/erlancompiler.py','kernel/src/pointer.e','-o',str(out/'pointer.c')],cwd=root,check=True)
exe=out/('test.exe' if os.name=='nt' else 'test')
subprocess.run([cc,'-O1','-Wno-parentheses-equality',f'-I{out}','-Ibuild/gen','-Ikernel/src','-Ierlan/runtime','tests/workspace/pointer.c',str(out/'pointer.c'),'build/gen/fb.c','-o',str(exe)],cwd=root,check=True)
subprocess.run([str(exe)],cwd=root,check=True)
