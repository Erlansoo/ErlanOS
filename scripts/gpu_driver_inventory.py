#!/usr/bin/env python3
"""Download/inspect NVIDIA's fixed Linux package AS DATA; never run its installer.

Python stdlib only. Download verifies the HTTPS-published SHA256 and a pinned
copy. Extraction accepts only directories and ordinary files in a fresh child
directory; it never recreates archive links, devices, or executable permissions.
"""
import argparse
import bisect
import collections
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import tarfile
import urllib.request

VERSION = "580.178.04"
FILENAME = f"NVIDIA-Linux-x86_64-{VERSION}-no-compat32.run"
BASE_URL = f"https://download.nvidia.com/XFree86/Linux-x86_64/{VERSION}/"
EXPECTED_SHA256 = "1833c8c5c53481dfab46df442c3de26ec5d4c35084e03d94ad93746d29899750"
MAX_DOWNLOAD = 512 * 1024**2
MAX_EXTRACT = 3 * 1024**3
MAX_FILE = 1024**3


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def download(root):
    root.mkdir(parents=True, exist_ok=True)
    checksums = {}
    for suffix in ("sha256sum", "md5sum"):
        with urllib.request.urlopen(BASE_URL + FILENAME + "." + suffix, timeout=60) as r:
            data = r.read(4096)
        (root / (FILENAME + "." + suffix)).write_bytes(data)
        checksums[suffix] = data.decode("ascii").split()[0]
    if checksums["sha256sum"] != EXPECTED_SHA256:
        raise ValueError("Official checksum differs from the reviewed version")
    target = root / FILENAME
    if not target.exists():
        pending = root / (FILENAME + ".partial")
        total = 0
        with urllib.request.urlopen(BASE_URL + FILENAME, timeout=120) as src, pending.open("wb") as dst:
            while chunk := src.read(4 * 1024**2):
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError("Download exceeds package bound")
                dst.write(chunk)
                print(f"download {total / 1024**2:.0f} MiB", flush=True)
        pending.replace(target)
    sha256, md5 = digest(target), digest(target, "md5")
    if sha256 != EXPECTED_SHA256 or md5 != checksums["md5sum"]:
        raise ValueError("Package checksum mismatch; extraction refused")
    result = {"url": BASE_URL + FILENAME, "version": VERSION,
              "bytes": target.stat().st_size, "sha256": sha256, "md5": md5,
              "official_sha256_match": True, "official_md5_match": True,
              "executed": False}
    (root / "provenance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def extract(root, offset):
    package = root / FILENAME
    if digest(package) != EXPECTED_SHA256:
        raise ValueError("Package SHA256 mismatch")
    dest = root / "extracted"
    if dest.exists():
        raise ValueError("Use a fresh extracted directory; refusing to overwrite")
    dest.mkdir()
    dest = dest.resolve()
    inventory, skipped, total, names = [], [], 0, set()
    with package.open("rb") as source:
        source.seek(offset)
        with tarfile.open(fileobj=source, mode="r|*") as archive:
            for member in archive:
                path = pathlib.PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or
                        any(re.search(r'[<>:"\\|?*\x00-\x1f]', p) or
                            p.endswith((".", " ")) or
                            p.split(".")[0].upper() in
                            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                             *(f"LPT{i}" for i in range(10))} for p in path.parts)):
                    raise ValueError(f"Unsafe archive path: {member.name!r}")
                target = dest.joinpath(*path.parts).resolve()
                if not target.is_relative_to(dest):
                    raise ValueError("Extraction escaped destination")
                folded = str(target).casefold()
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    skipped.append({"name": member.name, "type": member.type.decode("ascii", "replace"),
                                    "linkname": member.linkname})
                    continue
                if folded in names:
                    raise ValueError(f"Duplicate archive file: {member.name!r}")
                names.add(folded)
                total += member.size
                if member.size < 0 or member.size > MAX_FILE or total > MAX_EXTRACT or len(names) > 20000:
                    raise ValueError("Archive extraction bound exceeded")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, 1024**2)
                if target.stat().st_size != member.size:
                    raise ValueError("Truncated archive member")
                with target.open("rb") as f:
                    header = f.read(64)
                elf = None
                if header[:4] == b"\x7fELF":
                    endian = "<" if header[5] == 1 else ">"
                    elf = {"bits": {1: 32, 2: 64}.get(header[4]),
                           "type": struct.unpack_from(endian + "H", header, 16)[0],
                           "machine": struct.unpack_from(endian + "H", header, 18)[0]}
                inventory.append({"path": member.name, "bytes": member.size,
                                  "sha256": digest(target), "elf": elf})
    result = {"package_offset": offset, "total_extracted_bytes": total,
              "regular_files": len(inventory), "files": inventory, "skipped": skipped}
    (root / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("files", "skipped")}))
    print(f"ELF files: {sum(bool(i['elf']) for i in inventory)}; skipped non-files: {len(skipped)}")


def elf_summary(path):
    """Read ELF64 little-endian tables; no loading/linking/emulation of code."""
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected ELF64 little endian")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 58)
    if shsize != 64 or shoff + shsize * shnum > len(data):
        raise ValueError("Invalid ELF section bounds")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shsize) for i in range(shnum)]
    def payload(section):
        if section[1] == 8:  # SHT_NOBITS
            return b""
        start, length = section[4:6]
        if start + length > len(data):
            raise ValueError("Section beyond file")
        return data[start:start + length]
    def cstr(table, index):
        end = table.find(b"\0", index)
        if index >= len(table) or end < 0:
            raise ValueError("Invalid string table index")
        return table[index:end].decode("utf-8", "replace")
    shnames = payload(sections[shstrndx])
    symbols = {}
    for index, sec in enumerate(sections):
        if sec[1] in (2, 11):
            strings = payload(sections[sec[6]])
            raw = payload(sec)
            if sec[9] != 24 or len(raw) % 24:
                raise ValueError("Unexpected ELF symbol size")
            symbols[index] = []
            for off in range(0, len(raw), 24):
                name, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", raw, off)
                symbols[index].append({"name": cstr(strings, name), "bind": info >> 4,
                                       "type": info & 15, "section": shndx,
                                       "value": value, "size": size})
    all_symbols = [s for table in symbols.values() for s in table]
    functions = {}
    for sym in all_symbols:
        if sym["type"] == 2 and sym["section"]:
            functions.setdefault(sym["section"], []).append(sym)
    for table in functions.values():
        table.sort(key=lambda sym: sym["value"])
    relocations = collections.Counter()
    interesting = []
    for sec in sections:
        if sec[1] == 4:
            raw = payload(sec)
            if sec[9] != 24 or len(raw) % 24:
                raise ValueError("Unexpected ELF RELA size")
            table = symbols[sec[6]]
            for off in range(0, len(raw), 24):
                address, info, addend = struct.unpack_from("<QQq", raw, off)
                symbol = table[info >> 32]
                relocations[symbol["name"]] += 1
                if re.search(r"(rm_init_adapter|os_map_kernel_space|os_pci_read|os_alloc_mem|nv_get_firmware|rm_ioctl|nv_alloc_pages|nv_dma_map_alloc)", symbol["name"]):
                    row = {"section": cstr(shnames, sec[0]), "offset": address,
                           "type": info & 0xffffffff, "symbol": symbol["name"], "addend": addend}
                    table_functions = functions.get(sec[7], [])
                    index = bisect.bisect_right(table_functions, address, key=lambda sym: sym["value"]) - 1
                    if index >= 0:
                        caller = table_functions[index]
                        if address < caller["value"] + caller["size"]:
                            row.update(caller=caller["name"], caller_start=caller["value"],
                                       offset_in_caller=address-caller["value"])
                    interesting.append(row)
    result = {"path": str(path), "sha256": digest(path), "bytes": len(data),
              "sections": [{"name": cstr(shnames, sec[0]), "type": sec[1], "bytes": sec[5]} for sec in sections],
              "symbol_count": len(all_symbols),
              "undefined": sorted(set(s["name"] for s in all_symbols if s["section"] == 0 and s["name"])),
              "defined_function_count": sum(s["type"] == 2 and s["section"] != 0 for s in all_symbols),
              "anonymized_rm_symbols": sum(bool(re.fullmatch(r"_nv\d+rm", s["name"])) for s in all_symbols),
              "rela_count": sum(relocations.values()),
              "top_relocation_targets": relocations.most_common(50),
              "selected_relocations": interesting}
    return result, all_symbols


def analyze(root, llvm_dir):
    dest = root / "analysis"
    dest.mkdir(exist_ok=True)
    selected = []
    for pattern in ("nv-kernel.o_binary", "libcuda.so.*", "libnvidia-ptxjitcompiler.so.*"):
        selected.extend((root / "extracted").rglob(pattern))
    for path in selected:
        stem = path.relative_to(root / "extracted").as_posix().replace("/", "__")
        result, symbols = elf_summary(path)
        (dest / (stem + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        (dest / (stem + ".symbols.json")).write_text(json.dumps(symbols, indent=2) + "\n")
        for name, flags in (("headers", ["--file-headers", "--sections", "--needed-libs"]),):
            with (dest / (stem + "." + name + ".txt")).open("w") as out:
                subprocess.run([str(llvm_dir / "llvm-readobj.exe"), *flags, str(path)], stdout=out, check=True)
        exports = (["rm_init_adapter", "rm_ioctl", "rm_is_supported_pci_device", "_nv049768rm",
                    "_nv049742rm", "_nv049548rm", "_nv000790rm"]
                   if stem == "kernel__nvidia__nv-kernel.o_binary" else
                   ["cuInit", "cuMemAlloc_v2", "cuMemcpyHtoD_v2", "cuLaunchKernel", "cuModuleLoadDataEx"]
                   if path.name.startswith("libcuda.") else [])
        if exports:
            with (dest / (stem + ".selected.asm")).open("w") as out:
                subprocess.run([str(llvm_dir / "llvm-objdump.exe"), "--disassemble", "--reloc",
                                "--disassemble-symbols=" + ",".join(exports), str(path)], stdout=out, check=True)
        print(f"{stem}: {result['bytes']} bytes, {result['symbol_count']} symbols, "
              f"{len(result['undefined'])} imports, {result['rela_count']} RELA entries", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "extract", "analyze"))
    parser.add_argument("--root", type=pathlib.Path,
                        default=pathlib.Path("build/gpu-research/driver-" + VERSION))
    parser.add_argument("--offset", type=int, help="Reviewed byte offset of embedded tar stream")
    parser.add_argument("--llvm-dir", type=pathlib.Path, default=pathlib.Path("C:/Program Files/LLVM/bin"))
    args = parser.parse_args()
    if args.action == "download":
        download(args.root)
    elif args.action == "extract":
        if args.offset is None or args.offset < 1:
            parser.error("extract requires a positive reviewed --offset")
        extract(args.root, args.offset)
    else:
        analyze(args.root, args.llvm_dir)


if __name__ == "__main__":
    main()
