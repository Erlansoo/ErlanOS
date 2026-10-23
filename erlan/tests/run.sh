#!/usr/bin/env bash
# Pruebas en host del compilador Erlan. Uso: bash erlan/tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p build/erlan-tests
python erlan/erlancompiler.py erlan/tests/basics.e -o build/erlan-tests/basics.c --header build/erlan-tests/basics.h
clang -O1 -g -Wall -Wno-unused-variable -Ierlan/runtime build/erlan-tests/basics.c erlan/tests/host_panic.c -o build/erlan-tests/basics.exe
set +e
build/erlan-tests/basics.exe
code=$?
set -e
if [ "$code" != "42" ]; then echo "FALLO: se esperaba panic con código 42, salió $code"; exit 1; fi
echo "OK: erlan v0 basics"
