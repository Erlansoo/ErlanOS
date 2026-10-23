#!/usr/bin/env python3
"""Build a portable UEFI GPT/FAT32 disk without mounting or formatting a drive.

    python scripts/build_boot_image.py --model models/qwen3.5-2b-q8_0.gguf --output build/erlanos.img
    python scripts/build_boot_image.py --esp build/private/esp --output build/private/disk.img

The image contains only the selected model. Both GPT copies, FAT chains, directory
names and every file's SHA256 are checked from the finished image before promotion.
Only regular files below --esp are included; links and files >=4 GiB are rejected.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
SECTOR = 512
SPC = 8
CLUSTER = SECTOR * SPC
PART_START = 2048
RESERVED = 32
ESP_GUID = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le
Source = Path | bytes


@dataclass
class Node:
    name: str
    parent: Node | None = None
    source: Source | None = None
    children: dict[str, Node] = field(default_factory=dict)
    alias: bytes = b""
    first: int = 0
    clusters: int = 0
    size: int = 0

    @property
    def is_dir(self):
        return self.source is None

    @property
    def path(self):
        return (self.parent.path + "/" + self.name).lstrip("/") if self.parent else ""


def _source_size(source: Source) -> int:
    if isinstance(source, bytes):
        return len(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Not a regular boot file: {source}")
    return source.stat().st_size


def files_from_esp(directory: Path) -> dict[str, Source]:
    directory = directory.resolve(strict=True)
    files = {}
    for item in sorted(directory.rglob("*")):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError(f"Links are not supported in the boot image: {item}")
        if item.is_file():
            if not item.resolve().is_relative_to(directory):
                raise ValueError(f"ESP source escapes its directory: {item}")
            files[item.relative_to(directory).as_posix()] = item
    return files


def selected_boot_files(model: Path, config: Path | None = None) -> dict[str, Source]:
    """Select one GGUF, preserving the configured kernel and service modules."""
    model = model.resolve(strict=True)
    if model.suffix.lower() != ".gguf":
        raise ValueError("Selected model must be a .gguf file")
    config = config or ROOT / "kernel" / "limine.conf"
    original = config.read_text(encoding="utf-8")
    lines = [line for line in original.splitlines()
             if not re.match(r"\s*module_path:.*\.gguf\s*$", line, re.IGNORECASE)]
    lines.append(f"    module_path: boot():/boot/{model.name}")
    content = "\n".join(lines) + "\n"
    files: dict[str, Source] = {
        "EFI/BOOT/BOOTX64.EFI": ROOT / "third_party" / "limine" / "BOOTX64.EFI",
        "boot/limine.conf": content.encode("utf-8"),
        "boot/" + model.name: model,
    }
    for name in re.findall(r"^\s*(?:module_path|path):\s*boot\(\):/boot/([^\s]+)\s*$", content, re.MULTILINE):
        if PurePosixPath(name).name != name or "\\" in name:
            raise ValueError(f"Unsupported boot path: {name}")
        if name == model.name:
            continue
        if name == "kernel.elf":
            files["boot/" + name] = ROOT / "build" / name
        elif name.endswith(".bin"):
            files["boot/" + name] = ROOT / "build" / "user" / name
        else:
            raise ValueError(f"Unsupported configured module: {name}")
    if "boot/kernel.elf" not in files:
        raise ValueError("Boot configuration does not include kernel.elf")
    return files


def _alias(name: str, used: set[bytes]) -> bytes:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    clean = lambda text: re.sub(r"[^A-Z0-9_$~!#%&{}()@'`-]", "_", text.upper())
    base, extension = clean(stem), clean(ext)[:3]
    direct = (base[:8].ljust(8) + extension.ljust(3)).encode("ascii")
    if len(base) <= 8 and len(ext) <= 3 and direct not in used:
        used.add(direct)
        return direct
    for counter in range(1, 1000000):
        suffix = "~" + str(counter)
        value = (base[:8-len(suffix)] + suffix).ljust(8) + extension.ljust(3)
        alias = value.encode("ascii")
        if alias not in used:
            used.add(alias)
            return alias
    raise ValueError("Too many colliding FAT names")


def _long_entries(name: str, alias: bytes) -> list[bytes]:
    encoded = name.encode("utf-16-le")
    units = list(struct.unpack("<" + "H" * (len(encoded) // 2), encoded))
    if len(units) > 255:
        raise ValueError(f"FAT filename is too long: {name}")
    units.append(0)
    units += [0xffff] * ((-len(units)) % 13)
    checksum = 0
    for byte in alias:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 255
    result = []
    for index in reversed(range(len(units) // 13)):
        entry = bytearray(32)
        entry[0] = index + 1 | (0x40 if index == len(units) // 13 - 1 else 0)
        entry[11], entry[13] = 0x0f, checksum
        chunk = units[index*13:(index+1)*13]
        entry[1:11] = struct.pack("<5H", *chunk[:5])
        entry[14:26] = struct.pack("<6H", *chunk[5:11])
        entry[28:32] = struct.pack("<2H", *chunk[11:])
        result.append(bytes(entry))
    return result


def _short_entry(alias: bytes, directory: bool, cluster: int, size: int = 0):
    entry = bytearray(32)
    entry[:11] = alias
    entry[11] = 0x10 if directory else 0x20
    # Fixed valid DOS timestamp keeps contents deterministic except disk UUIDs.
    for offset in (16, 18, 24):
        struct.pack_into("<H", entry, offset, (2026-1980) << 9 | 1 << 5 | 1)
    struct.pack_into("<H", entry, 20, cluster >> 16)
    struct.pack_into("<HI", entry, 26, cluster & 0xffff, size)
    return bytes(entry)


def _gpt_header(current, backup, entry_lba, total, disk_guid, entries_crc):
    value = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x10000,
                                 92, 0, 0, current, backup, 34, total-34,
                                 disk_guid, entry_lba, 128, 128, entries_crc))
    struct.pack_into("<I", value, 16, zlib.crc32(value))
    return bytes(value).ljust(SECTOR, b"\0")


def build_image(output: Path, files: dict[str, Source], headroom_mib: int = 64) -> dict:
    """Stream a checked boot-file mapping into a new GPT/FAT32 raw disk file."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if headroom_mib < 1:
        raise ValueError("headroom_mib must be positive")
    root = Node("")
    nodes = [root]
    for path, source in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if not parts or path.startswith("/") or any(p in (".", "..") or "\\" in p for p in parts):
            raise ValueError(f"Invalid image path: {path}")
        node = root
        for part in parts[:-1]:
            if part not in node.children:
                node.children[part] = Node(part, node)
                nodes.append(node.children[part])
            node = node.children[part]
            if not node.is_dir:
                raise ValueError(f"File/directory conflict: {path}")
        if any(n.casefold() == parts[-1].casefold() for n in node.children):
            raise ValueError(f"Duplicate case-insensitive FAT name: {path}")
        child = Node(parts[-1], node, source=source, size=_source_size(source))
        if isinstance(source, Path) and source.resolve() in (output, output.with_name(output.name + ".partial")):
            raise ValueError("The output image cannot be one of its own input files")
        if child.size >= 2**32:
            raise ValueError(f"File exceeds FAT32's 4 GiB limit: {path}")
        node.children[parts[-1]] = child
        nodes.append(child)
    if not files:
        raise ValueError("Cannot build an empty boot image")
    for node in nodes:
        if node.is_dir:
            used: set[bytes] = set()
            count = 1 + (2 if node.parent else 0)
            folded = set()
            for child in node.children.values():
                if child.name.casefold() in folded:
                    raise ValueError(f"Case-insensitive directory collision: {child.path}")
                folded.add(child.name.casefold())
                child.alias = _alias(child.name, used)
                count += len(_long_entries(child.name, child.alias)) + 1
            node.clusters = math.ceil(count * 32 / CLUSTER)
        else:
            node.clusters = math.ceil(node.size / CLUSTER)
    used_clusters = sum(node.clusters for node in nodes)
    # At least 65,525 clusters are required for FAT32 classification.
    wanted = max(65525, used_clusters + math.ceil(headroom_mib * 1024**2 / CLUSTER))
    fat_sectors = math.ceil((wanted + 2) * 4 / SECTOR)
    part_sectors = math.ceil((RESERVED + fat_sectors * 2 + wanted * SPC) / 2048) * 2048
    while True:
        cluster_count = (part_sectors - RESERVED - fat_sectors * 2) // SPC
        required = math.ceil((cluster_count + 2) * 4 / SECTOR)
        if required <= fat_sectors:
            break
        fat_sectors = required
    total_sectors = PART_START + part_sectors + 2048
    fat = bytearray(fat_sectors * SECTOR)
    struct.pack_into("<II", fat, 0, 0x0ffffff8, 0xffffffff)
    next_cluster = 2
    for node in sorted(nodes, key=lambda n: not n.is_dir):
        if not node.clusters:
            continue
        node.first = next_cluster
        for cluster in range(next_cluster, next_cluster + node.clusters):
            struct.pack_into("<I", fat, cluster * 4,
                             cluster + 1 if cluster < next_cluster + node.clusters - 1 else 0x0fffffff)
        next_cluster += node.clusters
    if used_clusters > cluster_count:
        raise ValueError("Internal FAT32 capacity error")
    data_offset = (PART_START + RESERVED + fat_sectors * 2) * SECTOR
    cluster_offset = lambda cluster: data_offset + (cluster - 2) * CLUSTER
    entries = bytearray(128 * 128)
    struct.pack_into("<16s16sQQQ72s", entries, 0, ESP_GUID, uuid.uuid4().bytes_le,
                     PART_START, PART_START + part_sectors - 1, 0,
                     "ErlanOS".encode("utf-16-le").ljust(72, b"\0"))
    disk_guid = uuid.uuid4().bytes_le
    entries_crc = zlib.crc32(entries)
    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\0\x02\0", 0xee, b"\xff\xff\xff", 1,
                                min(total_sectors-1, 0xffffffff))
    mbr[510:] = b"\x55\xaa"
    boot = bytearray(SECTOR)
    boot[:11] = b"\xeb\x58\x90ERLANOS "
    struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, SPC, RESERVED, 2, 0, 0,
                     0xf8, 0, 63, 255, PART_START, part_sectors)
    struct.pack_into("<IHHIHH", boot, 36, fat_sectors, 0, 0, root.first, 1, 6)
    boot[64], boot[66] = 0x80, 0x29
    struct.pack_into("<I", boot, 67, zlib.crc32(disk_guid))
    boot[71:82] = b"ERLANOS    "
    boot[82:90] = b"FAT32   "
    boot[90:94] = b"\xfa\xf4\xeb\xfd"
    boot[510:] = b"\x55\xaa"
    fsinfo = bytearray(SECTOR)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<III", fsinfo, 484, 0x61417272, cluster_count-used_clusters, next_cluster)
    struct.pack_into("<I", fsinfo, 508, 0xaa550000)
    partial = output.with_name(output.name + ".partial")
    manifest = {"format": "GPT/FAT32", "image": str(output), "bytes": total_sectors*SECTOR,
                "partition_start_lba": PART_START, "partition_sectors": part_sectors,
                "cluster_bytes": CLUSTER, "files": []}
    with partial.open("wb") as image:
        image.truncate(total_sectors * SECTOR)
        def write(offset, value):
            image.seek(offset)
            image.write(value)
        write(0, mbr)
        write(SECTOR, _gpt_header(1, total_sectors-1, 2, total_sectors, disk_guid, entries_crc))
        write(2*SECTOR, entries)
        write((total_sectors-33)*SECTOR, entries)
        write((total_sectors-1)*SECTOR, _gpt_header(total_sectors-1, 1, total_sectors-33,
                                                 total_sectors, disk_guid, entries_crc))
        for sector in (0, 6):
            write((PART_START+sector)*SECTOR, boot)
        for sector in (1, 7):
            write((PART_START+sector)*SECTOR, fsinfo)
        for index in range(2):
            write((PART_START+RESERVED+index*fat_sectors)*SECTOR, fat)
        for node in nodes:
            if node.is_dir:
                records = []
                if node.parent:
                    records += [_short_entry(b".          ", True, node.first),
                                _short_entry(b"..         ", True, node.parent.first if node.parent.parent else 0)]
                for child in node.children.values():
                    records += _long_entries(child.name, child.alias)
                    records.append(_short_entry(child.alias, child.is_dir, child.first, child.size))
                write(cluster_offset(node.first), b"".join(records).ljust(node.clusters*CLUSTER, b"\0"))
                continue
            digest = hashlib.sha256()
            copied = 0
            source_file = io.BytesIO(node.source) if isinstance(node.source, bytes) else node.source.open("rb")
            with source_file:
                if node.first:
                    image.seek(cluster_offset(node.first))
                for block in iter(lambda: source_file.read(8*1024**2), b""):
                    image.write(block)
                    digest.update(block)
                    copied += len(block)
            if copied != node.size:
                raise ValueError(f"Source changed while copying: {node.path}")
            manifest["files"].append({"path": node.path, "bytes": copied, "sha256": digest.hexdigest()})
    verify_image(partial, manifest)
    partial.replace(output)
    manifest["verified"] = True
    output.with_name(output.name + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_image(path: Path, manifest: dict) -> None:
    """Read the on-disk GPT, FAT and long-name directories; hash every file."""
    with path.open("rb") as image:
        def read(offset, size):
            image.seek(offset)
            data = image.read(size)
            if len(data) != size:
                raise ValueError("Truncated boot image")
            return data
        total = path.stat().st_size // SECTOR
        mbr = read(0, SECTOR)
        if mbr[510:] != b"\x55\xaa" or mbr[450] != 0xee:
            raise ValueError("Missing GPT protective MBR")
        tables = []
        for lba in (1, total-1):
            header = bytearray(read(lba*SECTOR, 92))
            if header[:8] != b"EFI PART" or struct.unpack_from("<Q", header, 24)[0] != lba:
                raise ValueError("Invalid GPT header")
            crc = struct.unpack_from("<I", header, 16)[0]
            struct.pack_into("<I", header, 16, 0)
            if zlib.crc32(header) != crc:
                raise ValueError("Invalid GPT header CRC")
            entry_lba, count, size, checksum = struct.unpack_from("<QIII", header, 72)
            table = read(entry_lba*SECTOR, count*size)
            if zlib.crc32(table) != checksum:
                raise ValueError("Invalid GPT partition CRC")
            tables.append(table)
        if tables[0] != tables[1] or tables[0][:16] != ESP_GUID:
            raise ValueError("GPT copies disagree or partition is not an ESP")
        start, end = struct.unpack_from("<QQ", tables[0], 32)
        boot = read(start*SECTOR, SECTOR)
        bps, spc, reserved, copies = struct.unpack_from("<HBHB", boot, 11)
        sectors = struct.unpack_from("<I", boot, 32)[0]
        fat_size = struct.unpack_from("<I", boot, 36)[0]
        root = struct.unpack_from("<I", boot, 44)[0]
        cluster_size = bps*spc
        cluster_count = (sectors-reserved-copies*fat_size)//spc
        if bps != 512 or copies != 2 or sectors != end-start+1 or cluster_count < 65525:
            raise ValueError("Invalid FAT32 geometry")
        if boot != read((start+6)*SECTOR, SECTOR) or boot[510:] != b"\x55\xaa":
            raise ValueError("FAT32 boot sector copies disagree")
        fat = read((start+reserved)*SECTOR, fat_size*SECTOR)
        if fat != read((start+reserved+fat_size)*SECTOR, fat_size*SECTOR):
            raise ValueError("FAT copies disagree")
        info = read((start+1)*SECTOR, SECTOR)
        if info != read((start+7)*SECTOR, SECTOR) or struct.unpack_from("<I", info, 0)[0] != 0x41615252 or struct.unpack_from("<I", info, 484)[0] != 0x61417272:
            raise ValueError("Invalid FAT32 FSInfo sector")
        data_start = (start+reserved+copies*fat_size)*SECTOR
        allocated = set()
        def chain(first):
            seen = set()
            current = first
            while current < 0x0ffffff8:
                if current < 2 or current >= cluster_count+2 or current in seen or current in allocated:
                    raise ValueError("Invalid, crosslinked or circular FAT chain")
                seen.add(current)
                allocated.add(current)
                yield current
                current = struct.unpack_from("<I", fat, current*4)[0] & 0x0fffffff
        found = {}
        def walk(first, prefix):
            data = b"".join(read(data_start+(c-2)*cluster_size, cluster_size) for c in chain(first))
            long_parts = {}
            for offset in range(0, len(data), 32):
                entry = data[offset:offset+32]
                if entry[0] == 0:
                    break
                if entry[11] == 0x0f:
                    long_parts[entry[0]&0x1f] = entry[1:11]+entry[14:26]+entry[28:32]
                    continue
                if long_parts:
                    raw = b"".join(long_parts[k] for k in sorted(long_parts))
                    name = raw.decode("utf-16-le").split("\0")[0].rstrip("\uffff")
                    long_parts = {}
                else:
                    name = entry[:8].decode("ascii").rstrip() + ("." + entry[8:11].decode("ascii").rstrip() if entry[8:11].strip() else "")
                if name in (".", ".."):
                    continue
                first_cluster = struct.unpack_from("<H", entry, 20)[0] << 16 | struct.unpack_from("<H", entry, 26)[0]
                path = prefix + name
                if entry[11] & 0x10:
                    walk(first_cluster, path + "/")
                else:
                    size = struct.unpack_from("<I", entry, 28)[0]
                    digest = hashlib.sha256()
                    remaining = size
                    count = 0
                    for cluster in chain(first_cluster) if first_cluster else ():
                        count += 1
                        block = read(data_start+(cluster-2)*cluster_size, min(remaining, cluster_size))
                        digest.update(block)
                        remaining -= len(block)
                    if remaining or count != math.ceil(size/cluster_size):
                        raise ValueError(f"File FAT chain has wrong length: {path}")
                    found[path] = {"bytes": size, "sha256": digest.hexdigest()}
        walk(root, "")
        expected = {f["path"]: {"bytes": f["bytes"], "sha256": f["sha256"]} for f in manifest["files"]}
        if found != expected:
            raise ValueError("Image files do not match the input hashes or paths")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--esp", type=Path, help="Prepared ESP directory (copied into the raw FAT32 image)")
    source.add_argument("--model", type=Path, help="Selected GGUF; kernel/services come from build/")
    parser.add_argument("--config", type=Path, help="Limine configuration template for --model")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--headroom-mib", type=int, default=64)
    args = parser.parse_args()
    if args.config and args.esp:
        parser.error("--config is only supported with --model")
    files = files_from_esp(args.esp) if args.esp else selected_boot_files(args.model, args.config)
    result = build_image(args.output, files, args.headroom_mib)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
