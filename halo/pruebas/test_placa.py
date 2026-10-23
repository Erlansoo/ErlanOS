#!/usr/bin/env python3
"""Pruebas del motor de placa contra KiCad (corre con el Python de KiCad).

    "%LOCALAPPDATA%/Programs/KiCad/10.0/bin/python.exe" halo/pruebas/test_placa.py

1. Cobre por zonas en 4 capas, desde el esquemático del parpadeador: clase de red, capas con nombre
   propio, plano de GND, plano dividido (+5V con prioridad sobre GND en la misma capa), cobre de GND
   arriba y abajo, área prohibida, ruteo y costura. KiCad: DRC sin errores ni avisos, todo
   conectado y paridad idéntica.
2. Cara inferior: la posición prevista de cada pad coincide con la real en ambas caras y rotaciones.
3. Perfil: alturas de los modelos 3D y clasificación de calor, sensibilidad y señales rápidas.
4. Serigrafía: tras acomodar y girar una pieza a mano, KiCad no marca serigrafía encimada.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo.kicad import cobre as cu  # noqa: E402
from halo.kicad import perfil as pf  # noqa: E402
from halo.kicad.placa import Placa, mm, rotar_punto  # noqa: E402

SALIDA = REPO / "build" / "halo" / "pruebas" / "test_placa"
FUENTE = REPO / "halo" / "proyectos" / "parpadeo555"
fallos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"  [{'ok' if condicion else 'FALLO'}] {nombre}" + (f": {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def nuevo(nombre: str) -> Path:
    carpeta = SALIDA / nombre
    shutil.rmtree(carpeta, ignore_errors=True)
    carpeta.mkdir(parents=True)
    for extension in (".kicad_sch", ".kicad_pro"):
        shutil.copy2(FUENTE / f"parpadeo555{extension}", carpeta / f"parpadeo555{extension}")
    return carpeta


def prueba_cobre() -> None:
    print("1. Cobre por zonas en 4 capas")
    carpeta = nuevo("cobre")
    p = Placa(carpeta, "parpadeo555", capas=4)
    p.crear(45, 32)
    p.nombrar_capa("In1.Cu", "GND")
    p.nombrar_capa("In2.Cu", "VCC")
    r = p.acomodar()
    comprobar("el acomodo coloca todas las piezas", not r["faltan"], str(r))
    p.definir_clase("potencia", 0.5, 0.3, (0.9, 0.45), ["+5V", "GND"])
    p.plano("GND", "GND")
    p.cobre("+5V", ["In2.Cu"], region="cerca:U1,R1,R2,C3", prioridad=1, plano=True, nombre="ISLA_5V")
    p.cobre("GND", ["VCC"], plano=True)
    p.cobre("GND", ["F.Cu", "B.Cu"])
    x0, y0, x1, y1 = p.contorno_mm
    p.prohibir(p.capas_cobre(), (x1 - 7, y0 + 3, x1 - 2, y0 + 8))
    zonas = cu.zonas(p)
    cobre_zonas = [z for z in zonas if not z["regla"]]
    comprobar("cuatro zonas de cobre y un área prohibida", len(cobre_zonas) == 4 and len(zonas) == 5, str(zonas))
    comprobar("todas las zonas tienen cobre relleno", all(z["area_mm2"] > 50 for z in cobre_zonas),
              str([(z["red"], z["capas"], z["area_mm2"]) for z in cobre_zonas]))
    en_in2 = {z["red"]: z["area_mm2"] for z in cobre_zonas if z["capas"] == ["In2.Cu"]}
    en_in1 = next(z["area_mm2"] for z in cobre_zonas if z["capas"] == ["In1.Cu"])
    comprobar("plano dividido: la isla de +5V con prioridad le quita su región al GND de la misma capa",
              en_in2.get("+5V", 0) > 100 and en_in2.get("GND", 0) < en_in1 - en_in2.get("+5V", 0) * 0.5, str(en_in2))
    p.rutear()
    vias = p.costura("GND", 4.0)["vias"]
    comprobar("vías de costura de GND", vias >= 10, str(vias))
    anchos = {round(t["w"], 3) for t in p.pistas if t["red"] in ("+5V", "GND")}
    comprobar("las pistas de la clase potencia salen con su ancho", anchos <= {0.5}, str(anchos))
    d = p.verificar()
    comprobar("KiCad: DRC sin errores ni avisos", d["drc"] == {"errores": 0, "avisos": 0},
              str([v["description"] for v in d["violaciones"]][:5]))
    comprobar("KiCad: todo conectado", d["pendientes"] == 0,
              str([i["description"] for v in d["sin_conectar"] for i in v["items"]][:4]))
    comprobar("KiCad: paridad idéntica con el esquemático", d["paridad"] == 0)
    del p   # dos placas del mismo proyecto abiertas a la vez en un proceso comparten el proyecto de KiCad
    comprobar("los planos quedan guardados con la placa",
              Placa.abrir(carpeta, "parpadeo555").planos == {"In1.Cu": ["GND"], "In2.Cu": ["+5V", "GND"]})


def prueba_caras() -> None:
    print("2. Cara inferior")
    carpeta = nuevo("caras")
    p = Placa(carpeta, "parpadeo555")
    p.crear(60, 45)
    x0, y0 = p.contorno_mm[:2]
    casos = [("C1", "B", 0, 15, 12), ("C2", "B", 90, 30, 12), ("R3", "B", 270, 22, 28), ("R1", "B", 180, 45, 32),
             ("U1", "F", 90, 46, 12)]
    peor = 0.0
    for ref, lado, ang, dx, dy in casos:
        fp = p.huellas[ref]
        local = p._local_lados(fp, (lado,))[lado]
        p.colocar(ref, x0 + dx, y0 + dy, ang, avisar=False, lado=lado)
        reales = {pad.GetNumber(): (mm(pad.GetPosition().x), mm(pad.GetPosition().y)) for pad in fp.Pads()}
        for numero, px, py, _red in local["pads"]:
            ex, ey = rotar_punto(px, py, ang)
            peor = max(peor, abs(x0 + dx + ex - reales[numero][0]), abs(y0 + dy + ey - reales[numero][1]))
    comprobar("la posición prevista de los pads coincide con la real en ambas caras", peor < 1e-6, f"{peor}")
    comprobar("cada pieza quedó en la cara pedida", all(p.lado(p.huellas[r]) == lado for r, lado, *_ in casos))
    p.voltear("C1")
    comprobar("voltear pasa la pieza a la otra cara", p.lado(p.huellas["C1"]) == "F")


def prueba_perfil() -> None:
    print("3. Perfil de las piezas")
    p = Placa.abrir(SALIDA / "caras", "parpadeo555")
    perfiles = p.perfiles()
    comprobar("altura del DIP-8 leída de su modelo 3D", perfiles["U1"].fuente_altura == "modelo 3D"
              and 3.0 < perfiles["U1"].altura < 4.5, perfiles["U1"].resumen())
    comprobar("el electrolítico es sensible al calor", perfiles["C1"].sensible, perfiles["C1"].resumen())
    for ref, huella, valor, ancho, esperado in (
            ("Q1", "Package_TO_SOT_THT:TO-220-3_Vertical", "IRLZ44N", 0.3, 2),
            ("U3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", "AMS1117-3.3", 0.3, 2),
            ("Q2", "Package_TO_SOT_SMD:SOT-23", "2N7002", 0.3, 0),
            ("U4", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "LM358", 0.3, 0),
            ("D5", "Diode_SMD:D_SMC", "SMCJ24A", 0.3, 1),
            ("R9", "Resistor_SMD:R_0603_1608Metric", "10k", 0.8, 0)):
        nivel, motivo = pf.calor(ref, huella, valor, ancho)
        comprobar(f"calor de {ref} {valor}: {esperado}", nivel == esperado, f"{nivel} ({motivo})")
    rapidas = pf.redes_rapidas({"XTAL1": {("Y1", "1"), ("U1", "9")}, "USB_DP": {("J1", "3")}, "/CANH": {("U2", "7")},
                                "GND": {("Y1", "2")}, "LED": {("D1", "1")}}, lambda r: r == "GND")
    comprobar("redes rápidas: cristal, USB y CAN, no GND ni LED", rapidas == {"XTAL1", "USB_DP", "/CANH"}, str(rapidas))
    comprobar("pares diferenciales", pf.pares_diferenciales(["USB_DP", "USB_DM", "CAN_H", "CAN_L", "SDA"])
              == [("CAN_H", "CAN_L"), ("USB_DP", "USB_DM")])


def prueba_serigrafia() -> None:
    print("4. Serigrafía tras acomodar y girar a mano")
    carpeta = nuevo("serigrafia")
    p = Placa(carpeta, "parpadeo555")
    p.crear()
    p.acomodar()
    giradas, rechazos = 0, 0
    for ref in ("R1", "C2", "R3", "D1", "C1"):
        try:
            p.rotar(ref, 90, autor="usuario")
            giradas += 1
        except ValueError:
            rechazos += 1   # un choque o salirse del borde es un rechazo normal: el motor debe seguir usable
    try:
        p.fijar("R1")
        sigue = True
    except RuntimeError:   # ConflictoProyecto: la instancia quedó invalidada
        sigue = False
    comprobar("un rechazo por choque no bloquea el motor", sigue and p.etapa == "acomodada",
              f"giradas {giradas}, rechazos {rechazos}, etapa {p.etapa}, sigue {sigue}")
    r = p.corregir_serigrafia()
    comprobar("corregir serigrafía devuelve cuántas marcó y apartó", {"marcadas", "referencias", "rotulos"} <= set(r), str(r))
    d = p.verificar()
    serigrafia = [v for v in d["violaciones"] if v["type"].startswith("silk")]
    comprobar("tras corregir, KiCad no marca serigrafía encimada", not serigrafia,
              str([[i["description"] for i in v["items"]] for v in serigrafia][:4]))


def main() -> int:
    prueba_cobre()
    prueba_caras()
    prueba_perfil()
    prueba_serigrafia()
    if fallos:
        print(f"PLACA FAIL: {len(fallos)} comprobaciones fallaron: {fallos}")
        return 1
    print("PLACA PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
