"""Clases de red del proyecto de KiCad: ancho de pista, holgura y vía por red.

Se leen y escriben en net_settings del .kicad_pro, el mismo lugar donde KiCad las guarda: así el
DRC de KiCad juzga con las mismas reglas con las que la IA rutea y rellena cobre.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

PLANTILLA = {
    "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2,
    "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1, "name": "Default",
    "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647, "schematic_color": "rgba(0, 0, 0, 0.000)",
    "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
}


@dataclass(frozen=True)
class Regla:
    clase: str
    ancho: float
    holgura: float
    via_d: float
    via_t: float
    par_ancho: float = 0.0
    par_sep: float = 0.0


def coincide(patron: str, red: str) -> bool:
    if patron == red:
        return True
    return any(c in patron for c in "*?[") and fnmatch.fnmatchcase(red, patron)


class Clases:
    def __init__(self, pro: Path):
        self.pro = Path(pro)
        try:
            self.datos = json.loads(self.pro.read_text(encoding="utf-8")) if self.pro.is_file() else {}
        except (OSError, ValueError):
            self.datos = {}
        ns = self.datos.get("net_settings") or {}
        self.clases = {c.get("name"): c for c in ns.get("classes") or [] if c.get("name")}
        self.patrones = list(ns.get("netclass_patterns") or [])
        self.asignaciones = dict(ns.get("netclass_assignments") or {})

    def clase_de(self, red: str) -> str:
        asignada = self.asignaciones.get(red)
        if asignada:
            return str(asignada[0] if isinstance(asignada, list) else asignada)
        for p in self.patrones:
            if coincide(str(p.get("pattern", "")), red):
                return str(p.get("netclass"))
        return "Default"

    def regla(self, red: str, base: Regla) -> Regla:
        nombre = self.clase_de(red)
        c = self.clases.get(nombre) or self.clases.get("Default")
        if not c:
            return base
        return Regla(str(c.get("name", nombre)), float(c.get("track_width") or base.ancho),
                     float(c.get("clearance") or base.holgura), float(c.get("via_diameter") or base.via_d),
                     float(c.get("via_drill") or base.via_t), float(c.get("diff_pair_width") or 0.0),
                     float(c.get("diff_pair_gap") or 0.0))

    def definir(self, nombre: str, ancho: float | None = None, holgura: float | None = None,
                via: tuple[float, float] | None = None, patrones=(), base: Regla | None = None) -> dict:
        """Crea o ajusta una clase y le asigna redes por patrón (se admiten * y ?)."""
        ns = self.datos.setdefault("net_settings", {})
        ns.setdefault("meta", {"version": 4})
        clases = ns.setdefault("classes", [])
        if not any(c.get("name") == "Default" for c in clases):
            defecto = dict(PLANTILLA)
            if base:
                defecto.update(track_width=base.ancho, clearance=base.holgura, via_diameter=base.via_d, via_drill=base.via_t)
            clases.insert(0, defecto)
        clase = next((c for c in clases if c.get("name") == nombre), None)
        if clase is None:
            clase = dict(next(c for c in clases if c.get("name") == "Default"))
            clase["name"] = nombre
            clase["priority"] = len(clases) - 1
            clases.append(clase)
        if ancho:
            clase["track_width"] = float(ancho)
        if holgura:
            clase["clearance"] = float(holgura)
        if via:
            clase["via_diameter"], clase["via_drill"] = float(via[0]), float(via[1])
        lista = [p for p in (ns.get("netclass_patterns") or []) if p.get("pattern") not in set(patrones)]
        lista += [{"netclass": nombre, "pattern": p} for p in patrones]
        ns["netclass_patterns"] = lista
        self.pro.write_text(json.dumps(self.datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.__init__(self.pro)
        return self.clases[nombre]
