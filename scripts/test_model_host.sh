#!/usr/bin/env bash
# Compila el model runtime (Erlan) para el host y lo ejecuta con el GGUF de models/.
# Uso: bash scripts/test_model_host.sh ["mensaje"] [max_tokens]
set -euo pipefail
cd "$(dirname "$0")/.."
MSG="${1:-Who are you?}"
MAX="${2:-48}"
OUT=build/model-host
mkdir -p $OUT
for m in umath arena mathx gguf unicode tokenizer qwen35 matmul_fast llama chat; do
    python erlan/erlancompiler.py "services/model/$m.e" -o "$OUT/$m.c" --header "$OUT/$m.h" -Iservices/model -Iservices/lib -Imind
done
# plataforma de anfitrión (ganchos host_* en el arnés C)
python erlan/erlancompiler.py services/model/host/platform.e -o "$OUT/platform.c" --header "$OUT/platform.h" -Iservices/model/host -Iservices/model -Iservices/lib -Imind
clang -O3 -mavx2 -mno-fma -Wno-parentheses-equality -I"$OUT" -Ierlan/runtime -c "$OUT/matmul_fast.c" -o "$OUT/matmul_fast.o"
DISPATCH_FLAGS=()
if [[ "${ERLAN_MATMUL_PORTABLE:-0}" == 1 ]]; then DISPATCH_FLAGS=(-DERLAN_MATMUL_FORCE_PORTABLE); fi
clang -O3 -g -Wall "${DISPATCH_FLAGS[@]}" -Wno-parentheses-equality -Wno-unused-const-variable -Wno-unused-parameter -Wno-unused-variable \
    -Iservices/model/host -I$OUT -Iservices/model -Ierlan/runtime \
    $OUT/umath.c $OUT/platform.c $OUT/arena.c $OUT/mathx.c $OUT/gguf.c $OUT/unicode.c $OUT/tokenizer.c $OUT/qwen35.c $OUT/llama.c $OUT/chat.c \
    "$OUT/matmul_fast.o" services/model/host/main.c erlan/tests/host_panic.c -o $OUT/model_host.exe
$OUT/model_host.exe models/smollm2-135m-instruct-q8_0.gguf "$MSG" "$MAX"
