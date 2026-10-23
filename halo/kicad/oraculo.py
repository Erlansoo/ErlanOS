"""Oráculo: KiCad mismo juzga el esquemático.

- ERC con `kicad-cli sch erc --format json`.
- Netlist con `kicad-cli sch export netlist` y comparación exacta contra la intención
  (qué pines se quiso unir). Los nombres automáticos de red no importan; las particiones sí.
- SVG de KiCad para comparar a ojo con la vista del Halo.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import sexpr as sx
from .biblioteca import KICAD_DIR
from .verificador import ejecutar, informe_json

CLI = KICAD_DIR / "bin" / "kicad-cli.exe"


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([str(CLI), *args], capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def erc(sch: Path, salida: Path) -> dict:
    salida.parent.mkdir(parents=True, exist_ok=True)
    data = informe_json(["sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", str(salida), str(sch)],
                        salida, _run, ("sheets",))
    violaciones = []
    for hoja in data.get("sheets", []):
        for v in hoja.get("violations", []):
            violaciones.append({"severidad": v.get("severity"), "tipo": v.get("type"),
                                "descripcion": v.get("description"),
                                "elementos": [i.get("description") for i in v.get("items", [])],
                                "items": [{"uuid": i.get("uuid"), "pos": i.get("pos")} for i in v.get("items", [])]})
    errores = sum(1 for v in violaciones if v["severidad"] == "error")
    avisos = sum(1 for v in violaciones if v["severidad"] == "warning")
    return {"errores": errores, "avisos": avisos, "violaciones": violaciones,
            "kicad": data.get("kicad_version"), "informe": str(salida)}


def netlist(sch: Path, salida: Path) -> dict[str, set[tuple[str, str]]]:
    salida.parent.mkdir(parents=True, exist_ok=True)
    nueva = ejecutar(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(salida), str(sch)], salida, _run)
    raiz = sx.loads(nueva.read_text(encoding="utf-8"))
    if not isinstance(raiz, list) or raiz[0] != "export" or sx.find(raiz, "nets") is None:
        raise RuntimeError("KiCad entregó una netlist incompleta")
    nueva.replace(salida)
    redes = {}
    for net in sx.find_all(sx.find(raiz, "nets"), "net"):
        nombre = str(sx.val(net, "name", ""))
        nodos = {(str(sx.val(n, "ref")), str(sx.val(n, "pin"))) for n in sx.find_all(net, "node")}
        redes[nombre] = nodos
    return redes


def svg(sch: Path, carpeta: Path) -> Path | None:
    carpeta.mkdir(parents=True, exist_ok=True)
    _run(["sch", "export", "svg", "-o", str(carpeta), "--exclude-drawing-sheet", str(sch)])
    salidas = sorted(carpeta.glob("*.svg"))
    return salidas[0] if salidas else None


def comparar(intencion: dict[str, set], obtenidas: dict[str, set]) -> dict:
    """Compara particiones de pines reales (sin símbolos de potencia ni redes de un solo pin)."""
    def limpiar(redes):
        out = {}
        for nombre, nodos in redes.items():
            reales = frozenset(n for n in nodos if not n[0].startswith("#"))
            if len(reales) >= 2:
                out[reales] = nombre
        return out

    quiero, tengo = limpiar(intencion), limpiar(obtenidas)
    faltan = [sorted(p) for p in quiero if p not in tengo]
    sobran = [sorted(p) for p in tengo if p not in quiero]
    nombres = []
    for particion, nombre in quiero.items():
        if particion in tengo and not nombre.startswith("_") and tengo[particion] not in (nombre, "/" + nombre):
            nombres.append({"quise": nombre, "kicad": tengo[particion]})
    return {"coinciden": not faltan and not sobran, "redes": len(quiero), "faltan": faltan, "sobran": sobran,
            "diferencias": len(faltan) + len(sobran), "nombres_distintos": nombres}
