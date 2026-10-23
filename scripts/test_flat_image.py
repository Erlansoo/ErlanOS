#!/usr/bin/env python3
"""Verify the real user linker/objcopy path includes BSS beyond the old 64 KiB.

This inspects ELF section addresses and the resulting flat image without running
guest code. Optional --elf checks an additional service built by the Makefile.
"""
import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess


def tool(name):
    found = shutil.which(name)
    if not found and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / f"LLVM/bin/{name}.exe"
        if candidate.is_file():
            found = str(candidate)
    if not found:
        raise RuntimeError(f"{name} is required")
    return found


def verify(elf, binary, *, require_large=False):
    data = elf.read_bytes()
    assert data[:6] == b"\x7fELF\x02\x01", "expected ELF64 little-endian"
    entry = struct.unpack_from("<Q", data, 24)[0]
    table = struct.unpack_from("<Q", data, 40)[0]
    width, count, strings_index = struct.unpack_from("<HHH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, table + i * width) for i in range(count)]
    strings = sections[strings_index]
    names = data[strings[4]:strings[4] + strings[5]]
    named = {names[s[0]:].split(b"\0", 1)[0].decode(): s for s in sections}
    bss = named[".bss"]
    raw = binary.read_bytes()
    base = 0x400000
    assert entry == base, "flat loader enters at USER_BASE"
    assert len(raw) <= 0xFF0000 - base, "image overlaps user stack"
    assert bss[3] >= base and bss[3] + bss[5] - base <= len(raw), "BSS extends past flat image"
    assert not any(raw[bss[3] - base:bss[3] + bss[5] - base]), "BSS is not zero-initialized"
    if require_large:
        assert bss[5] > 65536, "regression fixture must exceed old BSS allowance"
    print(f"OK: {elf.name} BSS={bss[5]} bytes, flat={len(raw)} bytes, full BSS present and zero")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", action="append", type=Path, default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / "build/flat-image-test"
    out.mkdir(parents=True, exist_ok=True)
    clang, linker, objcopy = tool("clang"), tool("ld.lld"), tool("llvm-objcopy")
    obj, elf, binary = out / "fixture.o", out / "fixture.elf", out / "fixture.bin"
    subprocess.run([clang, "--target=x86_64-unknown-none-elf", "-ffreestanding", "-fno-stack-protector",
                    "-mno-red-zone", "-O1", "-c", str(root / "tests/m4/flat_image.c"), "-o", str(obj)], check=True)
    subprocess.run([linker, "-nostdlib", "-static", "-z", "max-page-size=0x1000", "-T",
                    str(root / "services/user.ld"), str(obj), "-o", str(elf)], check=True)
    subprocess.run([objcopy, "-O", "binary", str(elf), str(binary)], check=True)
    verify(elf, binary, require_large=True)
    for service in args.elf:
        verify(service, service.with_suffix(".bin"))


if __name__ == "__main__":
    main()
