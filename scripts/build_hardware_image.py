#!/usr/bin/env python3
"""Create a verified diagnostic disk FILE with Limine and the probe kernel only."""
import argparse
import json
from pathlib import Path

from build_boot_image import ROOT, build_image


def hardware_files(kernel: Path | None = None, name: str = "ErlanOS Hardware G0"):
    kernel = kernel or ROOT / "build/kernel-probe.elf"
    return {
        "EFI/BOOT/BOOTX64.EFI": ROOT / "third_party/limine/BOOTX64.EFI",
        "boot/kernel.elf": kernel,
        "boot/limine.conf": (
            "timeout: 0\nserial: yes\n\n/" + name + "\n"
            "    protocol: limine\n    path: boot():/boot/kernel.elf\n"
        ).encode("ascii"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/erlanos-hardware.img")
    parser.add_argument("--kernel", type=Path, default=None, help="kernel ELF (default: build/kernel-probe.elf)")
    parser.add_argument("--profile", default="hardware-g0")
    parser.add_argument("--name", default="ErlanOS Hardware G0", help="nombre de la entrada de Limine")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "build").resolve()) or output.suffix != ".img":
        parser.error("output must be an .img file within this workspace's build directory")
    manifest = build_image(output, hardware_files(args.kernel, args.name))
    manifest["profile"] = args.profile
    manifest["scope"] = ("PCI discovery only; no GPU compute, network driver or installation"
                         if args.profile == "hardware-g0" else
                         "e1000 driver + ARP/DHCP/DNS probe in QEMU user networking; no services, no model")
    output.with_suffix(".img.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
