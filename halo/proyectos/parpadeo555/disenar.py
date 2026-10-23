#!/usr/bin/env python3
"""Parpadeador LED con NE555, organizado en módulos como un esquemático profesional.

Tres bloques con título: entrada de 5 V, temporizador astable y indicador LED. Entre
bloques no hay cables largos: la alimentación va por símbolos de potencia y la señal
por la etiqueta global PULSO (salida en el temporizador, entrada en el indicador).

f = 1,44 / ((R1 + 2·R2)·C1) = 1,44 / ((1 k + 136 k)·10 µF) ≈ 1,05 Hz
I_LED = (5 V - 2 V) / 470 Ω ≈ 6,4 mA

    python halo/proyectos/parpadeo555/disenar.py [pausa_segundos]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from halo.kicad.esquematico import Esquematico  # noqa: E402
from halo.kicad.sesion import Sesion  # noqa: E402

AQUI = Path(__file__).resolve().parent
NOMBRE = "parpadeo555"
R_THT = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
C_THT = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"


def main() -> int:
    pausa = float(sys.argv[1]) if len(sys.argv) > 1 else 0.35
    sch, pcb = AQUI / f"{NOMBRE}.kicad_sch", AQUI / f"{NOMBRE}.kicad_pcb"
    archivos = {"esquematico": sch, **({"pcb": pcb} if pcb.is_file() else {})}
    sesion = Sesion("Parpadeador LED con NE555 (1 Hz)", sch, pausa=pausa, archivos=archivos)
    sesion.evento("Plan: tres módulos (entrada 5 V, temporizador NE555, indicador LED) unidos por "
                  "potencia y por la etiqueta global PULSO", "plan")
    e = Esquematico(AQUI, NOMBRE, titulo="Parpadeador LED NE555", sesion=sesion)

    sesion.halo("actuando", "organizando en módulos")
    e.bloque("Entrada de 5 V", 27.94, 60.96, 73.66, 96.52)
    e.bloque("Temporizador astable NE555", 101.6, 60.96, 190.5, 116.84)
    e.bloque("Indicador LED", 203.2, 71.12, 254.0, 111.76)

    # ---------------------------------------------------------------- entrada de 5 V
    sesion.halo("actuando", "entrada de alimentación")
    e.colocar("Connector_Generic:Conn_01x02", "J1", "Entrada 5V", 38.1, 76.2, espejo="y",
              huella="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    e.potencia("power:+5V", ("J1", "1"), distancia=12.7, hacia="arriba")
    e.potencia("power:PWR_FLAG", ("J1", "1"), distancia=5.08, hacia="arriba")
    e.potencia("power:GND", ("J1", "2"), distancia=12.7, hacia="abajo")
    e.potencia("power:PWR_FLAG", ("J1", "2"), distancia=5.08, hacia="abajo")
    e.nota("5 V regulados desde fuente externa", 30.48, 93.98)

    # ---------------------------------------------------------------- temporizador
    sesion.halo("actuando", "temporizador NE555")
    e.colocar("Timer:NE555P", "U1", "NE555P", 139.7, 86.36, huella="Package_DIP:DIP-8_W7.62mm")
    e.colocar("Device:R", "R1", "1k", 114.3, 76.2, huella=R_THT)
    e.colocar("Device:R", "R2", "68k", 114.3, 88.9, huella=R_THT)
    e.colocar("Device:C_Polarized", "C1", "10uF", 114.3, 101.6, huella="Capacitor_THT:CP_Radial_D5.0mm_P2.00mm")
    e.colocar("Device:C", "C2", "10nF", 167.64, 81.28, huella=C_THT)
    e.colocar("Device:C", "C3", "100nF", 177.8, 81.28, huella=C_THT)

    e.trazo("DESCARGA", (114.3, 80.01), (114.3, 85.09))
    e.trazo("DESCARGA", (129.54, 83.82), (114.3, 83.82))
    e.trazo("UMBRAL", (114.3, 92.71), (114.3, 97.79))
    e.trazo("UMBRAL", (129.54, 88.9), (124.46, 88.9), (124.46, 95.25), (114.3, 95.25))
    e.trazo("UMBRAL", (129.54, 91.44), (124.46, 91.44))
    e.trazo("CONTROL", (139.7, 76.2), (139.7, 66.04), (167.64, 66.04), (167.64, 77.47))

    e.potencia("power:+5V", ("R1", "1"))
    e.potencia("power:+5V", ("U1", "4"), distancia=2.54, hacia="arriba")
    e.potencia("power:+5V", ("U1", "8"))
    e.potencia("power:GND", ("U1", "1"))
    e.potencia("power:GND", ("C1", "2"))
    e.potencia("power:GND", ("C2", "2"))
    e.potencia("power:+5V", ("C3", "1"))
    e.potencia("power:GND", ("C3", "2"))
    e.etiqueta("PULSO", ("U1", "3"), distancia=2.54, tipo="global", forma="output")
    e.nota("f = 1,44 / ((R1 + 2·R2)·C1) ≈ 1,05 Hz", 124.46, 113.03)

    # ---------------------------------------------------------------- indicador LED
    sesion.halo("actuando", "indicador LED")
    e.colocar("Device:R", "R3", "470", 223.52, 86.36, rot=90, huella=R_THT)
    e.colocar("Device:LED", "D1", "LED rojo", 236.22, 95.25, rot=90, huella="LED_THT:LED_D5.0mm")
    e.etiqueta("PULSO", ("R3", "1"), distancia=5.08, tipo="global", forma="input")
    e.trazo("ANODO", (227.33, 86.36), (236.22, 86.36), (236.22, 91.44))
    e.potencia("power:GND", ("D1", "1"))
    e.nota("I_LED ≈ (5 − 2) V / 470 Ω ≈ 6,4 mA", 205.74, 109.22)

    resultado = e.verificar()
    print("ERC:", resultado["erc"], "| netlist:", resultado["netlist"], "| dibujo:", resultado["reglas"])
    for v in resultado["erc_detalle"]:
        print("  erc", v["severidad"], v["tipo"], "-", v["descripcion"], v["elementos"])
    for p in resultado["reglas_detalle"]["problemas"]:
        print("  dibujo:", p)
    if not resultado["netlist"]["coinciden"]:
        print("  netlist:", resultado["netlist_detalle"])
    ok = resultado["erc"]["errores"] == 0 and resultado["netlist"]["coinciden"] and not resultado["reglas"]["problemas"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
