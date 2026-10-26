#!/usr/bin/env python3
"""Exercise actual generated kernel primitives with hardware/process stubs."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", default="build/m4-kernel-host")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = (root / args.build).resolve()
    gen = out / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang")
    if not compiler and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LLVM/bin/clang.exe"
        if candidate.is_file():
            compiler = str(candidate)
    if not compiler:
        parser.error("clang is required (on Windows, install LLVM or source scripts/env.sh)")

    def run(command):
        subprocess.run([str(part) for part in command], cwd=root, check=True)

    # Generate all headers because module declarations include dependent headers.
    for module in sorted((root / "kernel/src").glob("*.e")):
        run([sys.executable, "erlan/erlancompiler.py", module, "-o", gen / f"{module.stem}.c",
             "--header", gen / f"{module.stem}.h", "-Ikernel/src", "-Inet", "-Inet/tls"])
    flags = [compiler, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Wno-unused-const-variable",
         "-Wno-unused-parameter", "-Wno-parentheses-equality", f"-I{gen}", "-Itests/m4/stubs", "-Ikernel/src",
         "-Ierlan/runtime", "-Iassets/fonts"]
    for name, source, modules in (
        ("m4-host", "kernel_host.c", ("syscall", "system", "vmm", "console", "fb", "keyboard", "document_view", "font", "docfont", "kmem")),
        ("proc-host", "proc_host.c", ("proc", "kmem")),
    ):
        executable = out / (name + (".exe" if os.name == "nt" else ""))
        run([*flags, f"tests/m4/{source}", *[gen / f"{module}.c" for module in modules],
             "-o", executable])
        run([executable])


if __name__ == "__main__":
    main()
