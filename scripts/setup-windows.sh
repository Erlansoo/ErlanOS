#!/usr/bin/env bash
# Toolchain nativo en Windows (Git Bash). No requiere virtualización: QEMU corre en TCG.
set -euo pipefail
cd "$(dirname "$0")/.."
winget install --id LLVM.LLVM -e --accept-source-agreements --accept-package-agreements --silent || true
winget install --id SoftwareFreedomConservancy.QEMU -e --accept-source-agreements --accept-package-agreements --silent || true
winget install --id ezwinports.make -e --accept-source-agreements --accept-package-agreements --silent || true
mkdir -p third_party
[ -d third_party/limine ] || git clone --depth=1 --branch=v9.x-binary https://github.com/limine-bootloader/limine.git third_party/limine
echo "listo. ahora:  source scripts/env.sh && make run"
