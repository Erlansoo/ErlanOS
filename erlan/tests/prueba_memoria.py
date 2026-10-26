#!/usr/bin/env python3
"""Pruebas del modelo de memoria de Erlan (docs/ERLAN-MEMORIA.md). Las corre `make test`.

1. erlan/tests/memoria.e debe compilar, enlazar con clang, ejecutarse y producir exactamente
   erlan/tests/memoria.esperado (orden de los drop, movimientos, préstamos).
2. Cada erlan/tests/errores/*.e debe ser RECHAZADO por erlancompiler con el mensaje que declara
   en su primera línea (`// espera: ...`), con línea y columna.

Uso: python erlan/tests/prueba_memoria.py   (desde la raíz, con clang en PATH: scripts/env.sh)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Windows: la consola y las tuberías van en cp1252; los mensajes del compilador llevan acentos.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ENV = dict(os.environ, PYTHONUTF8="1")

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "erlan" / "tests"
OUT = ROOT / "build" / "erlan-tests"
COMPILER = ROOT / "erlan" / "erlancompiler.py"


def compilar(fuente: Path, salida: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(COMPILER), str(fuente), "-o", str(salida),
                           "--header", str(salida.with_suffix(".h"))],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fallos = 0

    # 1. caso positivo con salida exacta
    c = OUT / "memoria.c"
    r = compilar(TESTS / "memoria.e", c)
    if r.returncode != 0:
        print(f"FALLO memoria.e no compila:\n{r.stderr}"); return 1
    exe = OUT / "memoria.exe"
    cc = subprocess.run(["clang", "-O1", "-g", "-Wall", "-Wno-unused-variable", "-Wno-parentheses-equality",
                         f"-I{ROOT / 'erlan' / 'runtime'}", str(c), str(TESTS / "host_panic.c"), "-o", str(exe)],
                        capture_output=True, text=True)
    if cc.returncode != 0:
        print(f"FALLO clang sobre memoria.c:\n{cc.stderr}"); return 1
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    esperado = (TESTS / "memoria.esperado").read_text(encoding="utf-8").replace("\r\n", "\n")
    obtenido = run.stdout.replace("\r\n", "\n")
    if run.returncode != 0 or obtenido != esperado:
        print(f"FALLO memoria.e: código {run.returncode}; salida distinta de memoria.esperado")
        import difflib
        print("".join(difflib.unified_diff(esperado.splitlines(True), obtenido.splitlines(True),
                                           "esperado", "obtenido")))
        fallos += 1
    else:
        print(f"OK  memoria.e: {len(obtenido.splitlines())} líneas, orden de drops y préstamos como se esperaba")

    # 2. casos que deben fallar con el mensaje declarado
    casos = sorted((TESTS / "errores").glob("*.e"))
    for caso in casos:
        primera = caso.read_text(encoding="utf-8").splitlines()[0]
        if not primera.startswith("// espera:"):
            print(f"FALLO {caso.name}: la primera línea debe ser '// espera: <mensaje>'"); fallos += 1; continue
        esperado = primera[len("// espera:"):].strip()
        r = compilar(caso, OUT / (caso.stem + ".c"))
        if r.returncode == 0:
            print(f"FALLO {caso.name}: compiló y debía fallar con «{esperado}»"); fallos += 1; continue
        linea = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        if esperado not in linea or ":0:0:" in linea.split(" error:")[0]:
            print(f"FALLO {caso.name}: se esperaba «{esperado}» con posición; salió: {linea}"); fallos += 1; continue
        print(f"OK  {caso.name}: {linea.split(' error: ', 1)[-1]}")
    print(f"{'OK' if not fallos else 'FALLO'}: modelo de memoria, {len(casos)} casos rechazados con su mensaje"
          if not fallos else f"FALLO: {fallos} prueba(s) del modelo de memoria")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
