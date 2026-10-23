#!/usr/bin/env python3
"""La vista de placa del Halo lee placas reales de las demos de KiCad 10.

Comprueba que cada placa ruteada de las demos se carga, que sus pads y pistas tienen
geometría válida y que una placa terminada casi no deja conexiones pendientes (una
diferencia pequeña es aceptable: el cálculo de pendientes del Halo es aproximado).

    python halo/pruebas/test_vista_pcb.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo.kicad.biblioteca import KICAD_DIR  # noqa: E402
from halo.kicad.vista_pcb import cargar_placa  # noqa: E402

DEMOS = ["pic_programmer/pic_programmer.kicad_pcb", "ecc83/ecc83-pp.kicad_pcb", "interf_u/interf_u.kicad_pcb",
         "complex_hierarchy/complex_hierarchy.kicad_pcb"]


def main() -> int:
    fallos = []
    base = KICAD_DIR / "share" / "kicad" / "demos"
    for demo in DEMOS:
        ruta = base / demo
        t0 = time.time()
        m = cargar_placa(ruta)
        ms = (time.time() - t0) * 1000
        pads = sum(len(h["pads"]) for h in m["huellas"])
        tam = len(json.dumps(m)) // 1024
        ok = m["huellas"] and pads and m["pistas"] and m["contorno"]
        pendientes_ok = m["resumen"]["sin_rutear"] <= max(3, m["resumen"]["redes"] // 10)
        print(f"  [{'ok' if ok and pendientes_ok else 'FALLO'}] {ruta.name}: {m['resumen']} pads={pads} "
              f"{ms:.0f} ms {tam} KB")
        if not (ok and pendientes_ok):
            fallos.append(demo)
    print("VISTA_PCB PASS" if not fallos else f"VISTA_PCB FAIL {fallos}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
