#!/usr/bin/env bash
# Pruebas en host del compilador Erlan. Uso: bash erlan/tests/run.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p build/erlan-tests
python erlan/erlancompiler.py erlan/tests/basics.e -o build/erlan-tests/basics.c --header build/erlan-tests/basics.h
clang -O1 -g -Wall -Wno-unused-variable -Wno-parentheses-equality -Ierlan/runtime build/erlan-tests/basics.c erlan/tests/host_panic.c -o build/erlan-tests/basics.exe
set +e
build/erlan-tests/basics.exe
code=$?
set -e
if [ "$code" != "42" ]; then echo "FALLO: se esperaba panic con código 42, salió $code"; exit 1; fi
echo "OK: erlan v0 basics"
# Modelo de memoria (propiedad, drop, préstamos): salida exacta y errores esperados.
python erlan/tests/prueba_memoria.py
# Programación de sistemas: import, asm, varargs, literales de array, atributos, datos de C.
python erlan/erlancompiler.py erlan/tests/sistema.e -o build/erlan-tests/sistema.c --header build/erlan-tests/sistema.h -Ierlan/tests/modulos
python erlan/erlancompiler.py erlan/tests/modulos/util.e -o build/erlan-tests/util.c --header build/erlan-tests/util.h
clang -O1 -g -Wall -Wno-unused-variable -Wno-parentheses-equality -Ierlan/runtime -Ierlan/tests -Ibuild/erlan-tests build/erlan-tests/sistema.c build/erlan-tests/util.c erlan/tests/host_panic.c -o build/erlan-tests/sistema.exe
build/erlan-tests/sistema.exe > build/erlan-tests/sistema.salida
if ! diff -q --strip-trailing-cr build/erlan-tests/sistema.salida erlan/tests/sistema.esperado >/dev/null; then echo "FALLO: sistema.e no produjo sistema.esperado"; diff --strip-trailing-cr build/erlan-tests/sistema.salida erlan/tests/sistema.esperado; exit 1; fi
echo "OK: sistema.e (import, asm, varargs, arrays, atributos)"
