#!/usr/bin/env python3
"""Compare ErlanOS G0's real PCI inventory with QEMU's independent QMP view.

Build ``make hardware-image`` first, then run this script. It boots a private,
model-free image, checks every BDF and assigned BAR, and saves a screenshot.
The emulated topology exercises bridges, multifunction devices, I/O, 32-bit
and 64-bit BARs. No host device, interactive VM, or physical disk is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from build_boot_image import build_image
from qemu_accel import accel_args
from qemu_m4_test import QemuSession, ROOT, TestFailure, common_arguments


TOPOLOGY = [
    "-nic", "none",
    "-device", "pcie-root-port,id=g0_root,bus=pcie.0,addr=0x4,slot=4,chassis=4",
    "-device", "pcie-pci-bridge,id=g0_bridge,bus=g0_root",
    "-device", "e1000e,id=g0_net0,bus=pcie.0,addr=0x5.0,multifunction=on,romfile=",
    "-device", "e1000e,id=g0_net1,bus=pcie.0,addr=0x5.1,romfile=",
    "-device", "edu,id=g0_edu,bus=g0_bridge,addr=0x2",
    "-device", "virtio-rng-pci,id=g0_64bit,bus=g0_bridge,addr=0x3,disable-legacy=on",
]
LINE = re.compile(r"^\[pci\] (dev|bar|bridge|end) (.+)$", re.MULTILINE)
FIELD = re.compile(r"([a-z_][a-z_0-9]*)=(0x[0-9a-fA-F]+|[0-9]+)(?=\s|$)")


def bdf(value, function="fn"):
    return value["bus"], value["slot"], value[function]


def label(address):
    return "%02x:%02x.%x" % address


def parse_inventory(text):
    records = {name: {} for name in ("dev", "bar", "bridge")}
    terminal = []
    if text.count("[pci] begin segment=0 buses=256 mode=conf1-readonly") != 1:
        raise TestFailure("Expected exactly one complete segment-0 read-only PCI scan")
    for match in LINE.finditer(text):
        kind, body = match.groups()
        row = {name: int(value, 0) for name, value in FIELD.findall(body)}
        if kind == "end":
            terminal.append(row)
            continue
        key = bdf(row)
        if kind == "bar":
            key = (*key, row["index"])
        if key in records[kind]:
            raise TestFailure(f"Duplicate {kind} record: {key}")
        records[kind][key] = row
    if len(terminal) != 1:
        raise TestFailure(f"Expected one PCI terminal marker, got {len(terminal)}")
    records["end"] = terminal[0]
    if terminal[0]["devices"] != len(records["dev"]):
        raise TestFailure("PCI end count does not match emitted function records")
    return records


def flatten_qmp(buses):
    devices = {}

    def visit(rows):
        for row in rows:
            key = bdf(row, "function")
            if key in devices:
                raise TestFailure(f"Duplicate BDF in QMP: {label(key)}")
            devices[key] = row
            visit(row.get("pci_bridge", {}).get("devices", []))

    for bus in buses:
        visit(bus["devices"])
    return devices


def compare_inventory(serial, query):
    found = parse_inventory(serial)
    expected = flatten_qmp(query)
    if found["dev"].keys() != expected.keys():
        missing = sorted(expected.keys() - found["dev"].keys())
        extra = sorted(found["dev"].keys() - expected.keys())
        raise TestFailure(f"BDF mismatch: missing={list(map(label, missing))}, extra={list(map(label, extra))}")
    expected_bars = {}
    expected_bridges = {}
    for address, source in expected.items():
        row = found["dev"][address]
        wanted = {"vendor": source["id"]["vendor"], "device": source["id"]["device"],
                  "class": source["class_info"]["class"] >> 8,
                  "subclass": source["class_info"]["class"] & 255,
                  "irq_pin": source["irq_pin"]}
        for field, value in wanted.items():
            if row.get(field) != value:
                raise TestFailure(f"{label(address)} {field}: kernel={row.get(field)} QMP={value}")
        if "pci_bridge" in source:
            bus = source["pci_bridge"]["bus"]
            expected_bridges[address] = {"primary": bus["number"], "secondary": bus["secondary"],
                                         "subordinate": bus["subordinate"]}
        for region in source["regions"]:
            # ROM BAR 6 is outside G0's ordinary header BARs. Unmapped regions
            # have address=-1 in QMP; G0 deliberately does not size/probe them.
            if region["bar"] >= 6 or region["address"] <= 0:
                continue
            kind = 1 if region["type"] == "io" else 3 if region.get("mem_type_64") else 2
            expected_bars[(*address, region["bar"])] = {
                "base": region["address"], "kind": kind, "prefetch": int(region.get("prefetch", False))}
    if found["bar"].keys() != expected_bars.keys():
        raise TestFailure(f"BAR set mismatch: kernel={sorted(found['bar'])}, QMP={sorted(expected_bars)}")
    for key, wanted in expected_bars.items():
        for field, value in wanted.items():
            if found["bar"][key].get(field) != value:
                raise TestFailure(f"BAR {key} {field}: kernel={found['bar'][key].get(field)} QMP={value}")
    if found["bridge"].keys() != expected_bridges.keys():
        raise TestFailure("Bridge set differs between kernel and QMP")
    for address, wanted in expected_bridges.items():
        for field, value in wanted.items():
            if found["bridge"][address].get(field) != value:
                raise TestFailure(f"Bridge {label(address)} {field} differs from QMP")
    targets = {
        "nvidia_gp107": sum((d["id"]["vendor"], d["id"]["device"]) == (0x10de, 0x1c82) for d in expected.values()),
        "intel_i219": sum((d["id"]["vendor"], d["id"]["device"]) == (0x8086, 0x15b8) for d in expected.values()),
    }
    for field, value in targets.items():
        if found["end"].get(field) != value:
            raise TestFailure(f"Target count {field} differs from QMP")
    if found["end"].get("invalid_bars") != 0:
        raise TestFailure(f"Unexpected invalid BARs in this valid QEMU topology: {found['end']}")
    by_name = {d["qdev_id"]: d for d in expected.values() if d["qdev_id"]}
    for name in ("g0_root", "g0_bridge", "g0_net0", "g0_net1", "g0_edu", "g0_64bit"):
        if name not in by_name:
            raise TestFailure(f"QEMU topology lacks required test device {name}")
    net0, net1 = by_name["g0_net0"], by_name["g0_net1"]
    if (net0["bus"], net0["slot"], net0["function"]) != (net1["bus"], net1["slot"], 0) or net1["function"] != 1:
        raise TestFailure("Test topology did not exercise two functions in the same slot")
    if by_name["g0_edu"]["bus"] == 0 or by_name["g0_64bit"]["bus"] == 0:
        raise TestFailure("Secondary-bus devices were not enumerated behind the bridges")
    kinds = {bar["kind"] for bar in expected_bars.values()}
    if not {1, 2, 3} <= kinds:
        raise TestFailure(f"Topology does not exercise IO, mem32 and mem64 BARs: {kinds}")
    return {"devices": len(expected), "bars": len(expected_bars), "bridges": len(expected_bridges),
            "targets": targets, "multifunction": True, "secondary_bus": True,
            "mem64_above_4g": sum(bar["kind"] == 3 and bar["base"] >= 2**32 for bar in expected_bars.values()),
            "bar_kinds": sorted(kinds), "bdfs": list(map(label, sorted(expected)))}


class PciSession(QemuSession):
    def prepare(self):
        config = b"timeout: 0\nserial: yes\n\n/ErlanOS hardware probe\n    protocol: limine\n    path: boot():/boot/kernel.elf\n"
        files = {"EFI/BOOT/BOOTX64.EFI": ROOT / "third_party/limine/BOOTX64.EFI",
                 "boot/limine.conf": config, "boot/kernel.elf": self.args.kernel.resolve(strict=True)}
        self.result["boot_image"] = build_image(self.directory / "disk.img", files)
        self.result["inputs"] = self.result["boot_image"]["files"]
        shutil.copy2(self.ovmf, self.directory / "firmware.fd")
        self.result["firmware"] = {"source": str(self.ovmf), "bytes": self.ovmf.stat().st_size,
            "sha256": hashlib.sha256((self.directory / "firmware.fd").read_bytes()).hexdigest()}

    def start(self):
        # QemuSession owns QMP, health checks and cleanup. Only launch topology
        # differs, keeping other suites' VM arguments and images unchanged.
        self.prepare()
        self.result["preparation_elapsed_s"] = round(time.monotonic() - self.started, 3)
        accelerator = accel_args(self.args.accel)
        if self.args.cpu:
            accelerator[accelerator.index("-cpu") + 1] = self.args.cpu
        command = [self.qemu, "-M", "q35", "-m", self.args.memory, *accelerator, *TOPOLOGY,
                   "-drive", "if=pflash,format=raw,readonly=on,file=firmware.fd",
                   "-drive", "format=raw,file=disk.img", "-serial", "file:serial.log",
                   "-qmp", "stdio", "-monitor", "none", "-display", "none", "-no-reboot", "-no-shutdown"]
        self.result["qemu_command"] = command
        self.stderr_file = self.stderr_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(command, cwd=self.directory, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr_file, text=True,
                                     encoding="utf-8", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.result["qemu_started_elapsed_s"] = round(time.monotonic() - self.started, 3)
        self.result["qemu_pid"] = self.proc.pid
        (self.directory / "qemu.pid").write_text(str(self.proc.pid) + "\n", encoding="ascii")
        threading.Thread(target=self._reader, daemon=True).start()
        greeting = self._message(15)
        if "QMP" not in greeting:
            raise TestFailure("Invalid QMP greeting")
        self.result["qmp_greeting"] = greeting
        self.execute("qmp_capabilities")
        print(f"[test] artifacts: {self.directory}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    common_arguments(parser)
    parser.set_defaults(memory="1G", timeout=180)
    parser.add_argument("--kernel", type=Path, default=ROOT / "build/kernel-probe.elf")
    parser.add_argument("--ready-marker", default="[hardware] probe: ready")
    args = parser.parse_args()
    if args.model or args.config:
        parser.error("PCI tests use a private model-free configuration; use --kernel to select a kernel")
    vm = PciSession(args, "pci")
    failure = None
    try:
        vm.start()
        serial = vm.wait_for(lambda text: args.ready_marker in text, "PCI scan and kernel readiness")
        vm.execute("stop")
        snapshot = vm.execute("query-pci")
        (vm.directory / "qmp-pci.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        vm.result["comparison"] = compare_inventory(serial, snapshot)
        vm.result["ready_marker"] = args.ready_marker
        vm.result["scope"] = "Emulated PCI inventory only; no physical NVIDIA access, MMIO mapping or GPU compute"
        print("[test] PCI comparison: " + json.dumps(vm.result["comparison"]), flush=True)
    except Exception as error:
        failure = error
    return vm.finish(failure)


if __name__ == "__main__":
    raise SystemExit(main())
