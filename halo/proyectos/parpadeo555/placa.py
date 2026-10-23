#!/usr/bin/env python3
"""Placa del parpadeador LED con NE555 con colocación decidida a mano por la IA.

Corre con el Python de KiCad (trae pcbnew):
    "%LOCALAPPDATA%/Programs/KiCad/10.0/bin/python.exe" halo/proyectos/parpadeo555/placa.py [pausa]

Decisiones: placa de dos capas 58,4 × 29,2 mm, todo THT en la cara superior, señales en
0,3 mm, alimentación en 0,5 mm, relleno de GND en la cara inferior.
El flujo por etapas (crear, acomodar automático, rutear, verificar) está en halo/kicad/pcb.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from halo.kicad.placa import Placa  # noqa: E402
from halo.kicad.sesion import Sesion  # noqa: E402

AQUI = Path(__file__).resolve().parent
NOMBRE = "parpadeo555"
COLOCACION = [
    ("J1", 102.87, 97.79, 0),
    ("C3", 111.76, 88.9, 0),
    ("U1", 121.92, 91.44, 0),
    ("R1", 134.62, 91.44, 0),
    ("R2", 134.62, 96.52, 0),   # alineada con THR (U1.6) y lejos del texto de R1
    ("C1", 149.86, 99.06, 0),
    ("C2", 134.62, 101.6, 0),
    ("R3", 111.76, 105.41, 0),
    ("D1", 130.81, 105.41, 180),
]


def main() -> int:
    pausa = float(sys.argv[1]) if len(sys.argv) > 1 else 0.35
    pcb, sch = AQUI / f"{NOMBRE}.kicad_pcb", AQUI / f"{NOMBRE}.kicad_sch"
    sesion = Sesion("Parpadeador LED con NE555 · placa", pcb, etapa="pcb", pausa=pausa,
                    archivos={"esquematico": sch, "pcb": pcb})
    sesion.evento("Plan: 2 capas, THT arriba, señales 0,3 mm, alimentación 0,5 mm, relleno de GND abajo", "plan")
    p = Placa(AQUI, NOMBRE, sesion=sesion, anchos={"+5V": 0.5, "GND": 0.5}, capas=2)

    sesion.halo("actuando", "contorno y huellas")
    p.crear(58.42, 29.21, origen=(99.06, 83.82))

    sesion.halo("actuando", "colocando componentes")
    for ref, x, y, rot in COLOCACION:
        p.colocar(ref, x, y, rot)

    sesion.halo("actuando", "trazando pistas")
    p.rutear()
    p.zona("GND", "B.Cu")

    r = p.verificar()
    print("DRC:", r["drc"], "| sin conectar:", r["pendientes"], "| paridad:", r["paridad"],
          "| pistas:", r["pistas"], "| vías:", r["vias"])
    for v in r["violaciones"]:
        print("  drc", v.get("severity"), v.get("type"), "-", v.get("description"),
              [i.get("description") for i in v.get("items", [])])
    for v in r["sin_conectar"]:
        print("  sin conectar:", v.get("description"), [i.get("description") for i in v.get("items", [])])
    for v in r["paridad_detalle"]:
        print("  paridad:", v.get("description"), [i.get("description") for i in v.get("items", [])])
    ok = r["drc"]["errores"] == 0 and r["pendientes"] == 0 and r["paridad"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
