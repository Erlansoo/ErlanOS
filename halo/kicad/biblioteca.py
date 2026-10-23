"""Símbolos de las bibliotecas oficiales de KiCad instaladas, aplanados como los guarda eeschema.

Un símbolo derivado (`extends`) se aplana: toma los dibujos y pines del padre, renombra
sus unidades con el nombre del hijo y conserva las propiedades del hijo.
"""
from __future__ import annotations

import copy
import math
import os
import re
from pathlib import Path

from . import sexpr as sx


def _kicad_dir() -> Path:
    env = os.environ.get("KICAD_DIR")
    if env:
        return Path(env)
    base = Path.home() / "AppData" / "Local" / "Programs" / "KiCad"
    candidatos = []
    for raiz in (base, Path(r"C:\Program Files\KiCad")):
        if raiz.is_dir():
            candidatos += [p for p in raiz.iterdir() if (p / "bin" / "kicad-cli.exe").is_file()]

    def clave(p: Path):
        return [int(x) if x.isdigit() else 0 for x in p.name.split(".")]

    candidatos.sort(key=clave, reverse=True)
    return candidatos[0] if candidatos else base / "10.0"


KICAD_DIR = _kicad_dir()
SIMBOLOS = KICAD_DIR / "share" / "kicad" / "symbols"
HUELLAS = KICAD_DIR / "share" / "kicad" / "footprints"
_BANDERAS = ("power", "pin_numbers", "pin_names", "exclude_from_sim", "in_bom", "on_board",
             "in_pos_files", "duplicate_pin_numbers_are_jumpers")


class Biblioteca:
    def __init__(self, carpeta: Path = SIMBOLOS):
        self.carpeta = Path(carpeta)
        self._libs: dict[str, dict] = {}
        self._planos: dict[str, list] = {}

    def _lib(self, nombre: str) -> dict:
        if nombre not in self._libs:
            ruta = self.carpeta / f"{nombre}.kicad_sym"
            if not ruta.is_file():
                raise KeyError(f"no existe la biblioteca {nombre} en {self.carpeta}")
            raiz = sx.loads(ruta.read_text(encoding="utf-8"))
            self._libs[nombre] = {str(s[1]): s for s in sx.find_all(raiz, "symbol")}
        return self._libs[nombre]

    def registrar(self, lib_id: str, nodo: list) -> None:
        """Usa el símbolo embebido en un esquemático existente (tal como lo guardó KiCad)."""
        self._planos[lib_id] = copy.deepcopy(nodo)

    def existe(self, lib_id: str) -> bool:
        if lib_id in self._planos:
            return True
        lib, nombre = lib_id.split(":", 1)
        try:
            return nombre in self._lib(lib)
        except KeyError:
            return False

    def plano(self, lib_id: str) -> list:
        """Copia del símbolo listo para `lib_symbols` del esquemático."""
        if lib_id not in self._planos:
            lib, nombre = lib_id.split(":", 1)
            tabla = self._lib(lib)
            if nombre not in tabla:
                raise KeyError(f"el símbolo {lib_id} no existe")
            nodo = copy.deepcopy(tabla[nombre])
            padre = sx.val(nodo, "extends")
            if padre:
                base = self.plano(f"{lib}:{padre}")
                hijo_props = {str(p[1]): p for p in sx.find_all(nodo, "property")}
                hijo_banderas = {str(h[0]): h for h in nodo[2:] if isinstance(h, list) and h and h[0] in _BANDERAS}
                resultado = [base[0], lib_id]
                ultimo_prop = 1
                for h in base[2:]:
                    if isinstance(h, list) and h and h[0] == "property":
                        resultado.append(hijo_props.pop(str(h[1]), h))
                        ultimo_prop = len(resultado) - 1
                    elif isinstance(h, list) and h and h[0] in _BANDERAS:
                        resultado.append(hijo_banderas.pop(str(h[0]), h))
                    elif isinstance(h, list) and h and h[0] == "symbol":
                        sub = copy.deepcopy(h)
                        sub[1] = re.sub(r"^" + re.escape(str(padre)) + r"_", nombre + "_", str(sub[1]))
                        resultado.append(sub)
                    else:
                        resultado.append(h)
                for prop in hijo_props.values():
                    ultimo_prop += 1
                    resultado.insert(ultimo_prop, prop)
                nodo = resultado
            else:
                nodo[1] = lib_id
            self._planos[lib_id] = nodo
        return copy.deepcopy(self._planos[lib_id])

    def _plano_lectura(self, lib_id: str) -> list:
        """El mismo símbolo sin copiar: para consultas (pines, banderas, propiedades), nunca para modificarlo."""
        if lib_id not in self._planos:
            self.plano(lib_id)
        return self._planos[lib_id]

    def _partes(self, lib_id: str, unidad: int, estilo: int):
        for sub in sx.find_all(self._plano_lectura(lib_id), "symbol"):
            m = re.match(r".*_(\d+)_(\d+)$", str(sub[1]))
            if m and int(m.group(1)) in (0, unidad) and int(m.group(2)) in (0, estilo):
                yield sub

    def pines(self, lib_id: str, unidad: int = 1, estilo: int = 1) -> list[dict]:
        clave = (lib_id, unidad, estilo)
        cache = self.__dict__.setdefault("_pines_cache", {})
        if clave in cache:
            return [dict(p) for p in cache[clave]]
        out = cache[clave] = self._pines(lib_id, unidad, estilo)
        return [dict(p) for p in out]

    def _pines(self, lib_id: str, unidad: int, estilo: int) -> list[dict]:
        out = []
        for parte in self._partes(lib_id, unidad, estilo):
            for pin in sx.find_all(parte, "pin"):
                at = sx.find(pin, "at")
                ang = float(at[3]) if len(at) > 3 else 0.0
                largo = float(sx.val(pin, "length", 2.54))
                x, y = float(at[1]), float(at[2])
                rad = math.radians(ang)
                out.append({"numero": str(sx.val(pin, "number", "")), "nombre": str(sx.val(pin, "name", "")),
                            "tipo": str(pin[1]), "at": (x, y), "ang": ang, "largo": largo,
                            "fin": (round(x + largo * math.cos(rad), 4), round(y + largo * math.sin(rad), 4)),
                            "oculto": sx.is_yes(pin, "hide")})
        return out

    def cuerpo(self, lib_id: str, unidad: int = 1, estilo: int = 1) -> tuple[float, float, float, float]:
        """Caja del dibujo del cuerpo en coordenadas de biblioteca (Y hacia arriba)."""
        pts = []
        for parte in self._partes(lib_id, unidad, estilo):
            for g in parte[1:]:
                if not isinstance(g, list):
                    continue
                if g[0] in ("polyline", "bezier"):
                    pts += [(float(p[1]), float(p[2])) for p in sx.find_all(sx.find(g, "pts"), "xy")]
                elif g[0] == "rectangle":
                    pts += [(float(sx.find(g, k)[1]), float(sx.find(g, k)[2])) for k in ("start", "end")]
                elif g[0] == "circle":
                    c, r = sx.find(g, "center"), float(sx.val(g, "radius", 0))
                    pts += [(float(c[1]) - r, float(c[2]) - r), (float(c[1]) + r, float(c[2]) + r)]
                elif g[0] == "arc":
                    pts += [(float(sx.find(g, k)[1]), float(sx.find(g, k)[2])) for k in ("start", "mid", "end")]
        if not pts:
            pts = [p["fin"] for p in self.pines(lib_id, unidad, estilo)] or [(0.0, 0.0)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def propiedad(self, lib_id: str, clave: str) -> dict:
        for prop in sx.find_all(self._plano_lectura(lib_id), "property"):
            if str(prop[1]) == clave:
                at = sx.find(prop, "at")
                efectos = sx.find(prop, "effects")
                just = sx.find(efectos, "justify") if efectos else None
                return {"valor": str(prop[2]), "at": (float(at[1]), float(at[2])),
                        "ang": float(at[3]) if len(at) > 3 else 0.0,
                        "justify": [str(a) for a in just[1:]] if just else [],
                        "oculto": sx.is_yes(prop, "hide") or (efectos is not None and sx.is_yes(efectos, "hide"))}
        return {"valor": "", "at": (0.0, 0.0), "ang": 0.0, "justify": [], "oculto": True}

    def bandera(self, lib_id: str, clave: str, defecto: str = "yes") -> str:
        return str(sx.val(self._plano_lectura(lib_id), clave, defecto))
