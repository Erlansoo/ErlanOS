#!/usr/bin/env python3
"""Run the real model input loop with deterministic keyboard events, no model."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
out = root / 'build/input-host'; out.mkdir(parents=True, exist_ok=True)
cc = shutil.which('clang') or 'C:/Program Files/LLVM/bin/clang.exe'
# Un stub por cada módulo Erlan que main.e importa, directa o transitivamente (y platform.h).
names = {'platform'}
for d in ('services/lib', 'services/model', 'mind'):
    names |= {p.stem for p in (root / d).glob('*.e')}
for name in sorted(names):
    (out / f'{name}.h').write_text('#include "frontend_stubs.h"\n', encoding='ascii')
subprocess.run([sys.executable, 'erlan/erlancompiler.py', 'services/model/main.e',
                '-o', str(out / 'service.c'), '-Iservices/lib', '-Iservices/model', '-Imind'], cwd=root, check=True)
flags = [cc, '-std=c11', '-O2', '-Wno-parentheses-equality', f'-I{out}',
         '-Itests/model/input_stubs', '-Ierlan/runtime']
subprocess.run([*flags, '-Dmain=model_service_entry', '-c', str(out / 'service.c'),
                '-o', str(out / 'service.o')], cwd=root, check=True)
exe = out / ('input.exe' if os.name == 'nt' else 'input')
subprocess.run([*flags, 'tests/model/input_frontend.c', str(out / 'service.o'), '-o', str(exe)], cwd=root, check=True)
subprocess.run([str(exe)], cwd=root, check=True)
