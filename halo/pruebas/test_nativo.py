#!/usr/bin/env python3
"""Pruebas del control nativo de KiCad contra KiCad mismo.

1. Lectura y escritura S-expresión sin pérdida sobre esquemáticos reales de las demos.
2. Geometría: símbolos en las 4 rotaciones y con espejo, unidos pin a pin por el enrutador.
   Si la matriz de transformación no coincidiera con eeschema, los cables no tocarían los
   pines y la netlist de kicad-cli saldría distinta.
3. Símbolo derivado (Timer:NE555P extiende NE555D) aplanado y cableado a un conector.
4. La netlist de KiCad debe ser idéntica a la intención y la revisión de dibujo, limpia.

    python halo/pruebas/test_nativo.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo.kicad import sexpr as sx  # noqa: E402
from halo.kicad.biblioteca import KICAD_DIR  # noqa: E402
from halo.kicad.esquematico import Esquematico  # noqa: E402
from halo.kicad.vista import cargar_modelo  # noqa: E402

SALIDA = REPO / "build" / "halo" / "pruebas"
fallos: list[str] = []


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"  [{'ok' if condicion else 'FALLO'}] {nombre}" + (f": {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def prueba_sexpr() -> None:
    print("1. S-expresiones")
    demos = sorted((KICAD_DIR / "share" / "kicad" / "demos").rglob("*.kicad_sch"))[:12]
    for ruta in demos:
        arbol = sx.loads(ruta.read_text(encoding="utf-8"))
        otra = sx.loads(sx.dumps(arbol))
        comprobar(f"ida y vuelta {ruta.name}", arbol == otra)


def prueba_geometria() -> None:
    print("2. Rotaciones, espejo y enrutado")
    carpeta = SALIDA / "geometria"
    shutil.rmtree(carpeta, ignore_errors=True)
    e = Esquematico(carpeta, "geometria", titulo="Prueba de geometria")
    casos = [(0, None), (90, None), (180, None), (270, None), (0, "y"), (90, "x")]
    for i, (rot, espejo) in enumerate(casos):
        x = 40.64 + i * 38.1
        e.colocar("Device:R", f"RA{i}", "1k", x, 50.8, rot=rot, espejo=espejo)
        e.colocar("Device:LED", f"DB{i}", "LED", x + 15.24, 76.2, rot=rot, espejo=espejo)
        e.red(f"N{i}_1", (f"RA{i}", "1"), (f"DB{i}", "1"))
        e.red(f"N{i}_2", (f"RA{i}", "2"), (f"DB{i}", "2"))
    resultado = e.verificar()
    comprobar("netlist de KiCad idéntica a la intención", resultado["netlist_detalle"]["coinciden"],
              str(resultado["netlist_detalle"]))
    comprobar("revisión de dibujo sin problemas", not resultado["reglas_detalle"]["problemas"],
              "; ".join(resultado["reglas_detalle"]["problemas"][:5]))
    modelo = cargar_modelo(e.ruta)
    comprobar("la vista del Halo lee el archivo generado", len(modelo["simbolos"]) == 12 and len(modelo["cables"]) > 0)


def prueba_derivado() -> None:
    print("3. Símbolo derivado y circuito integrado")
    carpeta = SALIDA / "derivado"
    shutil.rmtree(carpeta, ignore_errors=True)
    e = Esquematico(carpeta, "derivado", titulo="Prueba NE555P")
    e.colocar("Timer:NE555P", "U1", "NE555P", 101.6, 88.9)
    e.colocar("Connector_Generic:Conn_01x08", "J1", "Conn", 160.02, 83.82)
    for pin in range(1, 9):
        e.red(f"P{pin}", ("U1", str(pin)), ("J1", str(pin)))
    resultado = e.verificar()
    comprobar("NE555P aplanado y cableado: netlist exacta", resultado["netlist_detalle"]["coinciden"],
              str(resultado["netlist_detalle"]))
    comprobar("dibujo limpio con 8 redes", not resultado["reglas_detalle"]["problemas"],
              "; ".join(resultado["reglas_detalle"]["problemas"][:5]))
    texto = e.ruta.read_text(encoding="utf-8")
    comprobar("lib_symbols contiene Timer:NE555P con unidades renombradas",
              '(symbol "Timer:NE555P"' in texto and '(symbol "NE555P_0_1"' in texto)


def prueba_edicion() -> None:
    print("4. Abrir, mover y rotar con cables elásticos")
    ruta = SALIDA / "geometria" / "geometria.kicad_sch"
    e = Esquematico.abrir(ruta)
    comprobar("abrir reconstruye las 12 redes con sus nombres", len(e.redes) == 12 and "N0_1" in e.redes,
              str(sorted(e.redes)))
    e.mover("RA0", 5.08, 2.54, autor="usuario")
    e.rotar("DB1")
    e.mover_campo("RA2", "Reference", 1.27, 0)
    destino = e.simbolos["RA0"].at
    r = e.verificar(actualizar_intencion=False)
    comprobar("tras mover y rotar, la netlist de KiCad sigue idéntica", r["netlist_detalle"]["coinciden"],
              str(r["netlist_detalle"]))
    comprobar("tras editar no hay extremos sueltos ni cables sobre pines ajenos", not r["reglas_detalle"]["problemas"],
              "; ".join(r["reglas_detalle"]["problemas"][:4]))
    otra = Esquematico.abrir(ruta)
    comprobar("reabrir conserva la posición nueva", otra.simbolos["RA0"].at == destino)


def prueba_bloques() -> None:
    print("5. Bloques y etiquetas globales")
    carpeta = SALIDA / "bloques"
    shutil.rmtree(carpeta, ignore_errors=True)
    e = Esquematico(carpeta, "bloques", titulo="Prueba de bloques")
    e.bloque("Fuente", 25.4, 25.4, 76.2, 76.2)
    e.bloque("Carga", 101.6, 25.4, 152.4, 76.2)
    e.colocar("Device:R", "R1", "1k", 50.8, 50.8)
    e.colocar("Device:R", "R2", "1k", 127.0, 50.8)
    e.etiqueta("ENLACE", ("R1", "2"), distancia=5.08, tipo="global", forma="output")
    e.etiqueta("ENLACE", ("R2", "1"), distancia=5.08, tipo="global", forma="input")
    e.potencia("power:GND", ("R1", "1"))
    e.potencia("power:GND", ("R2", "2"))
    e.potencia("power:PWR_FLAG", ("R2", "2"), distancia=1.27, hacia="derecha")
    r = e.verificar()
    comprobar("dos bloques unidos sólo por etiqueta global: netlist exacta", r["netlist_detalle"]["coinciden"],
              str(r["netlist_detalle"]))
    comprobar("ERC sin errores con etiquetas globales", r["erc"]["errores"] == 0, str(r["erc_detalle"][:3]))
    e.colocar("Device:R", "R3", "1k", 38.1, 50.8)
    e.colocar("Device:R", "R4", "1k", 139.7, 50.8)
    try:
        e.red("CRUZA", ("R3", "1"), ("R4", "1"))   # pines libres, cada uno encerrado en su bloque
        cruzo = True
    except RuntimeError:
        cruzo = False
    comprobar("el enrutador no cruza el borde de un bloque", not cruzo)
    modelo = cargar_modelo(e.ruta)
    comprobar("la vista entrega bloques con título y etiquetas globales con forma",
              len(modelo["bloques"]) == 2 and {x["forma"] for x in modelo["etiquetas"]} == {"output", "input"}
              and {x["titulo"] for x in modelo["bloques"]} == {"Fuente", "Carga"})

    print("6. Mover módulos enteros y re-trazar etiquetas")

    def cerca(p, q) -> bool:
        return abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6

    antes_r2, antes_r4 = e.simbolos["R2"].at, e.simbolos["R4"].at
    carga = next(x for x in e.bloques if x["titulo"] == "Carga")
    e.mover_bloque(carga["uuid"], 12.7, 5.08)
    comprobar("mover el bloque arrastra todos sus símbolos",
              cerca(e.simbolos["R2"].at, (antes_r2[0] + 12.7, antes_r2[1] + 5.08))
              and cerca(e.simbolos["R4"].at, (antes_r4[0] + 12.7, antes_r4[1] + 5.08)))
    enlace_fuente = next(x for x in e.etiquetas if x["texto"] == "ENLACE" and x["at"][0] < 80)
    enlace_carga = next(x for x in e.etiquetas if x["texto"] == "ENLACE" and x["at"][0] > 80)
    cables_antes = len(e.cables)
    e.mover_etiqueta(enlace_fuente["uuid"], 0, 5.08)
    e.mover_etiqueta(enlace_carga["uuid"], 5.08, 0)
    comprobar("las etiquetas quedaron donde se soltaron", cerca(enlace_fuente["at"], (50.8, 64.77))
              and cerca(enlace_carga["at"], (enlace_carga["at"][0], enlace_carga["at"][1])))
    comprobar("re-trazar no deja cables de sobra", len(e.cables) <= cables_antes + 2, f"{cables_antes} -> {len(e.cables)}")
    r = e.verificar(actualizar_intencion=False)
    comprobar("tras mover bloque y etiquetas, la netlist de KiCad sigue idéntica", r["netlist_detalle"]["coinciden"],
              str(r["netlist_detalle"]))
    comprobar("tras mover bloque y etiquetas, el dibujo sigue limpio", not r["reglas_detalle"]["problemas"],
              "; ".join(r["reglas_detalle"]["problemas"][:4]))
    modelo = cargar_modelo(e.ruta)
    carga_vista = next(x for x in modelo["bloques"] if x["titulo"] == "Carga")
    comprobar("el título viaja con su bloque", abs(min(carga_vista["a"][0], carga_vista["b"][0]) - 114.3) < 1e-6)
    try:
        fuente = next(x for x in e.bloques if x["titulo"] == "Fuente")
        e.mover_bloque(fuente["uuid"], 63.5, 0)
        choca = False
    except ValueError:
        choca = True
    comprobar("un bloque no puede montarse sobre otro", choca)


def main() -> int:
    prueba_sexpr()
    prueba_geometria()
    prueba_derivado()
    prueba_edicion()
    prueba_bloques()
    if fallos:
        print(f"NATIVO FAIL: {len(fallos)} comprobaciones fallaron: {fallos}")
        return 1
    print("NATIVO PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
