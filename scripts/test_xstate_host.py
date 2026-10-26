#!/usr/bin/env python3
"""Verify scheduler FP defaults with native XSAVE/XRSTOR and SSE division."""
import os
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "build/xstate-host"
    out.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang")
    if not compiler and os.name == "nt":
        path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LLVM/bin/clang.exe"
        if path.is_file(): compiler = str(path)
    if not compiler: raise SystemExit("clang required")
    executable = out / ("xstate.exe" if os.name == "nt" else "xstate")
    # cpu_init_xstate, cpu_cpuid y cpu_xgetbv vienen de kernel/src/cpu.e (Erlan): se generan y
    # se enlazan en el host; las instrucciones privilegiadas de cpu.e no se llaman aquí.
    import sys
    subprocess.run([sys.executable, "erlan/erlancompiler.py", "kernel/src/cpu.e", "-o", str(out / "cpu.c"),
                    "--header", str(out / "cpu.h")], cwd=root, check=True)
    subprocess.run([compiler, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Wno-parentheses-equality", f"-I{out}", "-Ierlan/runtime",
                    "tests/cpu/xstate.c", str(out / "cpu.c"), "-o", str(executable)], cwd=root, check=True)
    subprocess.run([str(executable)], cwd=root, check=True)


if __name__ == "__main__": main()
