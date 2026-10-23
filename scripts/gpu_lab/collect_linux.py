#!/usr/bin/env python3
"""Collect a local, read-only Linux GPU lab inventory, without starting tracing.

Run on native Linux: python3 scripts/gpu_lab/collect_linux.py --output build/gpu-lab
Every run creates a new directory. No sudo, installation, mount, module loading,
device configuration, MMIO access, firmware extraction, or network is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
MAX_FILE = 2 * 1024 * 1024
CONFIG_KEYS = (
    "CONFIG_MMIOTRACE", "CONFIG_TRACING", "CONFIG_FTRACE", "CONFIG_TRACEPOINTS",
    "CONFIG_DEBUG_FS", "CONFIG_PCI", "CONFIG_PCI_MSI", "CONFIG_PCIEPORTBUS",
    "CONFIG_IOMMU_SUPPORT", "CONFIG_INTEL_IOMMU", "CONFIG_DRM_NOUVEAU",
    "CONFIG_MODULES", "CONFIG_MODULE_SIG", "CONFIG_MODULE_SIG_FORCE",
    "CONFIG_SECURITY_LOCKDOWN_LSM", "CONFIG_IKCONFIG", "CONFIG_IKCONFIG_PROC",
)
CONFIG_LINE = re.compile(r"^(?:# )?(" + "|".join(map(re.escape, CONFIG_KEYS)) + r")(?==| is not set$)")
PCI_FILES = ("vendor", "device", "subsystem_vendor", "subsystem_device", "class",
             "revision", "irq", "resource", "numa_node", "boot_vga",
             "current_link_speed", "current_link_width", "max_link_speed", "max_link_width")
TOOLS = ("lspci", "nvidia-smi", "modinfo", "cc", "gcc", "clang", "make", "cmake",
         "nvcc", "cuobjdump", "nvdisasm", "demmio", "envydis", "valgrind")


def read_text(path, limit=MAX_FILE):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
        return {"path": str(path), "text": data[:limit].decode("utf-8", errors="replace"),
                "truncated": len(data) > limit}
    except OSError as error:
        return {"path": str(path), "unavailable": str(error)}


def run_query(arguments, timeout):
    """Only callers' fixed query commands reach this function; never a shell."""
    started = time.monotonic()
    executable = shutil.which(arguments[0])
    record = {"argv": arguments}
    if executable is None:
        return {**record, "unavailable": "command not installed"}
    record["executable"] = executable
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    try:
        response = subprocess.run([executable, *arguments[1:]], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout, env=environment, check=False)
        record.update({"exit_code": response.returncode,
                       "stdout": response.stdout[:MAX_FILE].decode("utf-8", errors="replace"),
                       "stderr": response.stderr[:MAX_FILE].decode("utf-8", errors="replace"),
                       "truncated": len(response.stdout) > MAX_FILE or len(response.stderr) > MAX_FILE})
    except subprocess.TimeoutExpired as error:
        record.update({"unavailable": f"query exceeded {timeout}s",
                       "stdout": (error.stdout or b"")[:MAX_FILE].decode("utf-8", errors="replace"),
                       "stderr": (error.stderr or b"")[:MAX_FILE].decode("utf-8", errors="replace")})
    except OSError as error:
        record["unavailable"] = str(error)
    record["elapsed_s"] = round(time.monotonic() - started, 3)
    return record


def kernel_config(release):
    sources = (Path("/proc/config.gz"), Path("/boot") / ("config-" + release))
    attempts = []
    for path in sources:
        try:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                text = handle.read(MAX_FILE + 1)
            if len(text) > MAX_FILE:
                attempts.append({"path": str(path), "unavailable": "config exceeds size bound"})
                continue
            return {"path": str(path), "selected_lines": [line for line in text.splitlines()
                    if CONFIG_LINE.match(line)], "previous_attempts": attempts}
        except (OSError, EOFError) as error:
            attempts.append({"path": str(path), "unavailable": str(error)})
    return {"unavailable": "No readable kernel configuration", "attempts": attempts}


def pci_inventory():
    directory = Path("/sys/bus/pci/devices")
    inventory = []
    try:
        devices = sorted(directory.iterdir())
    except OSError as error:
        return {"unavailable": str(error)}
    for device in devices:
        if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]", device.name):
            continue
        fields = {name: read_text(device / name, 8192) for name in PCI_FILES}
        try:
            driver = (device / "driver").resolve(strict=True).name
        except OSError:
            driver = None
        # Read only kernel-exported text attributes. In particular do not open
        # config, rom, resourceN, enable, reset, remove or any driver bind file.
        inventory.append({"bdf": device.name, "driver": driver, "fields": fields})
    return inventory


def collect(timeout):
    release = platform.release()
    commands = [
        ["lspci", "-Dnnk"], ["lspci", "-Dvvnn", "-d", "10de:"],
        ["nvidia-smi", "--query-gpu=pci.bus_id,pci.device_id,name,driver_version,memory.total,pstate,clocks.current.graphics,clocks.current.memory,power.draw,power.limit", "--format=csv"],
        ["nvidia-smi", "--query-supported-clocks=gpu_name,memory,graphics", "--format=csv"],
        *[["modinfo", "-F", field, module] for module in ("nvidia", "nouveau")
          for field in ("filename", "version", "license", "vermagic", "signer")],
        *[[name, "--version"] for name in ("cc", "gcc", "clang", "nvcc", "cuobjdump", "nvdisasm")],
    ]
    modules = read_text("/proc/modules")
    if "text" in modules:
        modules["text"] = "\n".join(line for line in modules["text"].splitlines()
                                      if line.split()[0].startswith(("nvidia", "nouveau")))
    traces = []
    for base in (Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing")):
        traces.append({"base": str(base), "exists": base.is_dir(), "files": {
            name: read_text(base / name, 16384)
            for name in ("available_tracers", "current_tracer", "tracing_on", "buffer_size_kb")}})
    queries = [run_query(command, timeout) for command in commands]
    return {
        "schema": "erlanos-gpu-lab-inventory-v1", "captured_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Read-only inventory; no MMIO trace captured and no compute workload executed",
        "system": {"os": platform.system(), "release": release, "version": platform.version(),
                   "machine": platform.machine(), "euid": os.geteuid(),
                   "wsl_detected": "microsoft" in release.lower()},
        "os_release": read_text("/etc/os-release", 16384), "kernel_config": kernel_config(release),
        "loaded_gpu_modules": modules, "pci": pci_inventory(), "tracefs": traces,
        "lockdown": read_text("/sys/kernel/security/lockdown", 16384),
        "tools": {name: shutil.which(name) for name in TOOLS}, "queries": queries,
        "limitations": ["Missing or denied queries are reported, not repaired automatically.",
                        "Unavailable MMIOTrace does not prove the hardware lacks tracing support.",
                        "NVIDIA-SMI queries do not establish whether MMIOTrace covers the selected driver.",
                        "WSL and ordinary QEMU do not expose the physical GPU as a native Linux PCI device."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/gpu-lab", help="Parent for a NEW run directory")
    parser.add_argument("--query-timeout", type=float, default=15, help="Seconds per fixed read-only command")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.exit(2, "Este inventario requiere Linux nativo. Windows no permite observar aquí el MMIO del controlador Linux.\n")
    if not 1 <= args.query_timeout <= 60:
        parser.error("--query-timeout must be between 1 and 60 seconds")
    args.output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ-"), dir=args.output))
    report = collect(args.query_timeout)
    report_path = output / "inventory.json"
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    report_path.write_bytes(payload)
    (output / "manifest.json").write_text(json.dumps({"files": [{"name": "inventory.json", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}]}, indent=2) + "\n", encoding="utf-8")
    print(f"Inventario guardado localmente: {report_path}")
    print("No se ha activado MMIOTrace ni ejecutado cómputo en la GPU.")
    if report["system"]["wsl_detected"]:
        print("WSL detectado: este inventario no valida acceso PCI nativo a la GTX física.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
