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
    subprocess.run([compiler, "-std=c11", "-O1", "-Wall", "-Wextra", "-Ikernel/src", "-Ierlan/runtime",
                    "tests/cpu/xstate.c", "-o", str(executable)], cwd=root, check=True)
    subprocess.run([str(executable)], cwd=root, check=True)


if __name__ == "__main__": main()
