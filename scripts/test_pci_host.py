#!/usr/bin/env python3
"""Validate real Erlan PCI inventory with synthetic config space and audit I/O assembly."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", default="build/pci-host")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = (root / args.build).resolve()
    gen = out / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    clang = shutil.which("clang")
    if not clang and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LLVM/bin/clang.exe"
        if candidate.is_file():
            clang = str(candidate)
    if not clang:
        parser.error("clang is required")

    def run(parts):
        subprocess.run([str(p) for p in parts], cwd=root, check=True)

    run([sys.executable, "erlan/erlancompiler.py", "kernel/src/pci.e", "-o", gen / "pci.c",
         "--header", gen / "pci.h"])
    executable = out / ("pci-host.exe" if os.name == "nt" else "pci-host")
    run([clang, "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
         "-Wno-parentheses-equality", f"-I{gen}", "-Itests/pci/stubs", "-Ikernel/src",
         "-Ierlan/runtime", "tests/pci/pci_host.c", gen / "pci.c", "-o", executable])
    run([executable])
    assembly = out / "shim.s"
    run([clang, "--target=x86_64-unknown-none-elf", "-std=c11", "-O2", "-S",
         "-ffreestanding", "-mno-red-zone", "-Ikernel/src", "-Ierlan/runtime",
         "tests/pci/shim_probe.c", "-o", assembly])
    # The native shim contains privileged instructions; it is compiled but is
    # never executed by a Windows/Linux host test process. See audit below.
    sys.path.insert(0, str(root / "tests/pci"))
    from shim_audit import audit
    audit(assembly)
    audit(assembly, symbol="pci_test_shim_write", write_value=0x0ABCDEF0)
    print(f"PASS PCI host suite; assembly audit: {assembly}")


if __name__ == "__main__":
    main()
