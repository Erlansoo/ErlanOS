#!/usr/bin/env bash
# Build native chat; content quality and generation controls are separate suites.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${ERLAN_CHAT_BUILD:-build/chat-host}"
MODEL="${ERLAN_MODEL:-models/smollm2-135m-instruct-q8_0.gguf}"
if [[ "${1:-}" == "--model" ]]; then MODEL="$2"; shift 2; fi
mkdir -p "$OUT"
for m in umath arena mathx gguf unicode tokenizer qwen35 matmul_fast llama chat; do
    python erlan/erlancompiler.py "services/model/$m.e" -o "$OUT/$m.c" --header "$OUT/$m.h" -Iservices/model -Iservices/lib -Imind
done
# plataforma de anfitrión (ganchos host_* en el arnés C)
python erlan/erlancompiler.py services/model/host/platform.e -o "$OUT/platform.c" --header "$OUT/platform.h" -Iservices/model/host -Iservices/model -Iservices/lib -Imind
clang -O3 -mavx2 -mno-fma -Wno-parentheses-equality -I"$OUT" -Ierlan/runtime -c "$OUT/matmul_fast.c" -o "$OUT/matmul_fast.o"
DISPATCH_FLAGS=()
if [[ "${ERLAN_MATMUL_PORTABLE:-0}" == 1 ]]; then DISPATCH_FLAGS=(-DERLAN_MATMUL_FORCE_PORTABLE); fi
clang -O3 -g -Wall -DERLAN_HOST_CANCEL_TEST "${DISPATCH_FLAGS[@]}" -Wno-parentheses-equality -Wno-unused-const-variable -Wno-unused-parameter -Wno-unused-variable \
    -Iservices/model/host -Iservices/model -I"$OUT" -Ierlan/runtime \
    "$OUT"/umath.c "$OUT"/platform.c "$OUT"/arena.c "$OUT"/mathx.c "$OUT"/gguf.c "$OUT"/unicode.c "$OUT"/tokenizer.c "$OUT"/qwen35.c "$OUT"/llama.c "$OUT"/chat.c \
    "$OUT/matmul_fast.o" services/model/host/chat_test.c erlan/tests/host_panic.c -o "$OUT/chat_test.exe"
if [[ "${1:-}" == "--build-only" ]]; then exit 0; fi
if [[ "${1:-}" == "--semantic" ]]; then
    shift
    python scripts/test_chat_semantics.py --exe "$OUT/chat_test.exe" --model "$MODEL" "$@"
else
    "$OUT/chat_test.exe" "$MODEL" "$@"
fi
