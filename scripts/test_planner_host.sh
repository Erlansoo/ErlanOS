#!/usr/bin/env bash
# Compile and exercise the real Erlan planner with SmolLM2, without actions.
# source scripts/env.sh && bash scripts/test_planner_host.sh [request ...]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=build/planner-host
mkdir -p "$OUT"
for m in arena mathx gguf unicode tokenizer qwen35 matmul_fast llama chat planner; do
    python erlan/erlancompiler.py "services/model/$m.e" -o "$OUT/$m.c" --header "$OUT/$m.h"
done
clang -O3 -mavx2 -mno-fma -Wno-parentheses-equality -I"$OUT" -Ierlan/runtime -c "$OUT/matmul_fast.c" -o "$OUT/matmul_fast.o"
DISPATCH_FLAGS=()
if [[ "${ERLAN_MATMUL_PORTABLE:-0}" == 1 ]]; then DISPATCH_FLAGS=(-DERLAN_MATMUL_FORCE_PORTABLE); fi
clang -O3 -g -Wall "${DISPATCH_FLAGS[@]}" -Wno-parentheses-equality -Wno-unused-const-variable -Wno-unused-parameter -Wno-unused-variable \
    -Iservices/model/host -I"$OUT" -Iservices/model -Ierlan/runtime \
    "$OUT"/arena.c "$OUT"/mathx.c "$OUT"/gguf.c "$OUT"/unicode.c "$OUT"/tokenizer.c "$OUT"/qwen35.c \
    "$OUT"/llama.c "$OUT"/chat.c "$OUT"/planner.c \
    "$OUT/matmul_fast.o" services/model/host/plan.c erlan/tests/host_panic.c -o "$OUT/planner_host.exe"
if [ "$#" -eq 0 ]; then
    set -- --self-test
fi
"$OUT/planner_host.exe" models/smollm2-135m-instruct-q8_0.gguf "$@"
