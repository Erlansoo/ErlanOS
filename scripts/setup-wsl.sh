#!/usr/bin/env bash
# Prepara el toolchain de ErlanOS dentro de WSL2 Ubuntu.
# Uso (desde WSL):  bash scripts/setup-wsl.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== paquetes apt =="
sudo apt-get update
sudo apt-get install -y clang lld llvm make git qemu-system-x86 ovmf gdb python3

echo "== Limine (binarios prebuild, rama v9.x-binary) =="
mkdir -p third_party
if [ ! -d third_party/limine ]; then
    git clone --depth=1 --branch=v9.x-binary https://github.com/limine-bootloader/limine.git third_party/limine
else
    git -C third_party/limine pull --ff-only
fi

echo "== verificación =="
clang --version | head -1
ld.lld --version | head -1
qemu-system-x86_64 --version | head -1
ls /usr/share/OVMF/ 2>/dev/null || ls /usr/share/ovmf/ 2>/dev/null || true
if [ -w /dev/kvm ]; then echo "KVM: disponible"; else echo "KVM: no disponible (QEMU usará TCG, más lento)"; fi

echo
echo "listo. ahora:  make run"
