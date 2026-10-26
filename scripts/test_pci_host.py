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

    # pci.e importa pci_io y serial: se generan sus headers (sin compilar su C en el host;
    # pci_host.c aporta las funciones de puerto sintéticas y kprintf).
    for module in ("pci", "pci_io", "serial", "cpu"):
        run([sys.executable, "erlan/erlancompiler.py", f"kernel/src/{module}.e", "-o", gen / f"{module}.c",
             "--header", gen / f"{module}.h", "-Ikernel/src"])
    executable = out / ("pci-host.exe" if os.name == "nt" else "pci-host")
    run([clang, "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
         "-Wno-parentheses-equality", f"-I{gen}", "-Ikernel/src",
         "-Ierlan/runtime", "tests/pci/pci_host.c", gen / "pci.c", "-o", executable])
    run([executable])
    # El shim privilegiado real es kernel/src/pci_io.e (Erlan): se compila a ensamblador con
    # las opciones del kernel y se audita; nunca se ejecuta en el anfitrión.
    assembly = out / "pci_io.s"
    run([clang, "--target=x86_64-unknown-none-elf", "-std=gnu11", "-O2", "-S",
         "-ffreestanding", "-mno-red-zone", f"-I{gen}", "-Ikernel/src", "-Ierlan/runtime",
         "-Wno-parentheses-equality", gen / "pci_io.c", "-o", assembly])
    sys.path.insert(0, str(root / "tests/pci"))
    from shim_audit import audit
    audit(assembly, symbol="pci_conf1_read32")
    audit(assembly, symbol="pci_conf1_write32", write_value=0x0ABCDEF0)
    print(f"PASS PCI host suite; assembly audit: {assembly}")


if __name__ == "__main__":
    main()
