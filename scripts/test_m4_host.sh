#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=build/m4-host
mkdir -p "$OUT"
python erlan/erlancompiler.py services/lib/ai_wire.e -o "$OUT/ai_wire.c" --header "$OUT/ai_wire.h"
clang -O2 -Wall -Wextra -Wno-unused-const-variable -Ierlan/runtime -I"$OUT" \
    "$OUT/ai_wire.c" tests/m4/wire.c erlan/tests/host_panic.c -o "$OUT/wire.exe"
"$OUT/wire.exe"
python scripts/test_m4_kernel_host.py
