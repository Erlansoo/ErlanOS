"""Convierte un .kicad_pcb en lista de dibujo para el Halo, con las conexiones pendientes.

Coordenadas de placa en mm (Y hacia abajo). Una huella en (at X Y θ) lleva un punto local
(x, y) a (X + x·cosθ + y·sinθ, Y − x·sinθ + y·cosθ); el ángulo de un pad ya es total.

Zonas de cobre: cada una llega con su red, capas, prioridad, nombre, tipo de conexión y contorno,
y con su relleno si KiCad ya lo calculó. Las áreas de reglas (prohibidas) van con sus reglas.
Las conexiones pendientes (ratsnest) unen, por red, pads, pistas, vías y cobre relleno que se
tocan; si una zona aún no tiene relleno se usa su contorno como aproximación. Los grupos que
quedan separados se enlazan por su par más cercano.
"""
from __future__ import annotations

import bisect
import math
from pathlib import Path

from . import sexpr as sx
from .vista import _arc_path

CAPAS_COBRE = ("F.Cu", "B.Cu")


def _r(v) -> float:
    return round(float(v), 4)


def _xy(n) -> tuple[float, float]:
    return (float(n[1]), float(n[2]))


_NOMBRES_RED: dict[int, str] = {}


def _red(nodo) -> str:
    """Nombre de la red: KiCad 10 guarda (net "nombre"); versiones previas, (net N) o (net N "nombre")."""
    net = sx.find(nodo, "net")
    if net is None:
        return str(sx.val(nodo, "net_name", ""))
    if isinstance(net[-1], str):
        return str(net[-1])
    if isinstance(net[-1], int):
        return _NOMBRES_RED.get(net[-1], "")
    return ""


class _Transformada:
    def __init__(self, at):
        self.x, self.y = float(at[1]), float(at[2])
        self.ang = float(at[3]) if len(at) > 3 else 0.0
        self.c, self.s = math.cos(math.radians(self.ang)), math.sin(math.radians(self.ang))

    def __call__(self, p) -> tuple[float, float]:
        x, y = p
        return (self.x + x * self.c + y * self.s, self.y - x * self.s + y * self.c)


def _pad_dentro(pad, x, y, holgura=0.0) -> bool:
    dx, dy = x - pad["p"][0], y - pad["p"][1]
    if pad["forma"] == "circle":
        return math.hypot(dx, dy) <= pad["w"] / 2 + holgura
    a = math.radians(pad["ang"])
    c, s = math.cos(a), math.sin(a)
    px, py = dx * c - dy * s, dx * s + dy * c
    return abs(px) <= pad["w"] / 2 + holgura and abs(py) <= pad["h"] / 2 + holgura


def _dist_segmento(x, y, a, b) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - a[0]) * vx + (y - a[1]) * vy) / l2))
    return math.hypot(x - (a[0] + t * vx), y - (a[1] + t * vy))


def _area_poligono(pts) -> float:
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


class _Raster:
    """Polígonos cortados en filas finas: responde rápido si un punto cae dentro del cobre."""

    def __init__(self, poligonos, paso: float = 0.2):
        self.paso = paso
        todos = [p for poly in poligonos for p in poly]
        self.filas: dict[int, list[float]] = {}
        if not todos:
            self.caja = (0, 0, 0, 0)
            return
        self.caja = (min(p[0] for p in todos), min(p[1] for p in todos), max(p[0] for p in todos), max(p[1] for p in todos))
        self.y0 = self.caja[1]
        for poly in poligonos:
            n = len(poly)
            for i in range(n):
                (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
                if ya == yb:
                    continue
                if ya > yb:
                    xa, ya, xb, yb = xb, yb, xa, ya
                for f in range(max(0, math.ceil((ya - self.y0) / paso - 0.5)), math.floor((yb - self.y0) / paso - 0.5) + 1):
                    yc = self.y0 + (f + 0.5) * paso
                    if ya <= yc < yb:
                        self.filas.setdefault(f, []).append(xa + (yc - ya) * (xb - xa) / (yb - ya))
        for xs in self.filas.values():
            xs.sort()

    def cerca(self, x, y, margen) -> bool:
        x0, y0, x1, y1 = self.caja
        return x0 - margen <= x <= x1 + margen and y0 - margen <= y <= y1 + margen

    def dentro(self, x, y) -> bool:
        if not self.filas or y < self.y0:
            return False
        xs = self.filas.get(int((y - self.y0) / self.paso))
        return bool(xs) and bisect.bisect_right(xs, x) % 2 == 1


def _toca_pad(raster: _Raster, pad) -> bool:
    """Con alivio térmico el centro queda en el hueco: se prueban también puntos junto al borde del pad."""
    x, y = pad["p"]
    if not raster.cerca(x, y, max(pad["w"], pad["h"]) + 1):
        return False
    if raster.dentro(x, y):
        return True
    a = math.radians(pad["ang"])
    c, s = math.cos(a), math.sin(a)
    for g in (0.1, 0.3):
        hx, hy = pad["w"] / 2 + g, pad["h"] / 2 + g
        for lx, ly in ((hx, 0), (-hx, 0), (0, hy), (0, -hy), (hx, hy), (-hx, hy), (hx, -hy), (-hx, -hy)):
            if raster.dentro(x + lx * c + ly * s, y - lx * s + ly * c):
                return True
    return False


def _toca_circulo(raster: _Raster, p, radio) -> bool:
    x, y = p
    if not raster.cerca(x, y, radio + 1):
        return False
    if raster.dentro(x, y):
        return True
    for g in (0.1, 0.3):
        r = radio + g
        if any(raster.dentro(x + r * math.cos(k * math.pi / 4), y + r * math.sin(k * math.pi / 4)) for k in range(8)):
            return True
    return False


def _grafico(nodo, T, capa):
    tipo = nodo[0]
    stroke = sx.find(nodo, "stroke")
    w = float(sx.val(stroke, "width", 0.1)) if stroke else float(sx.val(nodo, "width", 0.1) or 0.1)
    relleno = str(sx.val(nodo, "fill", "no")) in ("yes", "solid")
    if tipo in ("fp_line", "gr_line"):
        a, b = T(_xy(sx.find(nodo, "start"))), T(_xy(sx.find(nodo, "end")))
        return {"t": "poly", "pts": [[_r(a[0]), _r(a[1])], [_r(b[0]), _r(b[1])]], "w": w, "capa": capa}
    if tipo in ("fp_rect", "gr_rect"):
        (x0, y0), (x1, y1) = _xy(sx.find(nodo, "start")), _xy(sx.find(nodo, "end"))
        pts = [T(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
        return {"t": "poly", "pts": [[_r(p[0]), _r(p[1])] for p in pts], "w": w, "capa": capa, "relleno": relleno}
    if tipo in ("fp_circle", "gr_circle"):
        c = T(_xy(sx.find(nodo, "center")))
        e = T(_xy(sx.find(nodo, "end")))
        return {"t": "circle", "c": [_r(c[0]), _r(c[1])], "r": _r(math.hypot(e[0] - c[0], e[1] - c[1])), "w": w,
                "capa": capa, "relleno": relleno}
    if tipo in ("fp_arc", "gr_arc"):
        s, m, e = (T(_xy(sx.find(nodo, k))) for k in ("start", "mid", "end"))
        return {"t": "path", "d": _arc_path(s, m, e), "w": w, "capa": capa}
    if tipo in ("fp_poly", "gr_poly"):
        pts = [T(_xy(p)) for p in sx.find_all(sx.find(nodo, "pts"), "xy")]
        return {"t": "poly", "pts": [[_r(p[0]), _r(p[1])] for p in pts] + ([[_r(pts[0][0]), _r(pts[0][1])]] if pts else []),
                "w": w, "capa": capa, "relleno": relleno}
    return None


def _orden_capa(nombre: str) -> int:
    if nombre == "F.Cu":
        return 0
    if nombre == "B.Cu":
        return 999
    try:
        return int(nombre[2:-3])
    except ValueError:
        return 500


def _capas_de(nodo, cobre) -> list[str]:
    lista = []
    capa = sx.find(nodo, "layer")
    if capa is not None and len(capa) > 1:
        lista.append(str(capa[1]))
    varias = sx.find(nodo, "layers")
    if varias is not None:
        lista += [str(c) for c in varias[1:] if isinstance(c, str)]
    salida = []
    for c in lista:
        if c == "*.Cu":
            salida += list(cobre)
        elif c == "F&B.Cu":
            salida += ["F.Cu", "B.Cu"]
        else:
            salida.append(c)
    return list(dict.fromkeys(salida))


def cargar_placa(path: Path | str) -> dict:
    global CAPAS_COBRE
    path = Path(path)
    raiz = sx.loads(path.read_text(encoding="utf-8"))
    if not raiz or raiz[0] != "kicad_pcb":
        raise ValueError(f"{path} no es una placa de KiCad")
    _NOMBRES_RED.clear()
    for net in sx.find_all(raiz, "net"):
        if len(net) >= 3 and isinstance(net[1], int):
            _NOMBRES_RED[net[1]] = str(net[2])
    nombres, nombres_capa = [], {}
    for c in (sx.find(raiz, "layers") or [])[1:]:
        if isinstance(c, list) and len(c) > 1 and str(c[1]).endswith(".Cu"):
            nombres.append(str(c[1]))
            if len(c) > 3 and isinstance(c[3], str) and str(c[3]) != str(c[1]):
                nombres_capa[str(c[1])] = str(c[3])
    CAPAS_COBRE = tuple(sorted(nombres, key=_orden_capa)) or ("F.Cu", "B.Cu")
    ident = _Transformada([None, 0, 0, 0])
    modelo = {"tipo": "pcb", "archivo": str(path), "nombre": path.stem, "capas": list(CAPAS_COBRE),
              "nombres_capa": nombres_capa, "contorno": [], "graficos": [], "huellas": [], "pistas": [], "vias": [],
              "zonas": [], "areas": [], "ratsnest": []}
    todos = []
    pads_global = []

    for nodo in raiz[1:]:
        if not isinstance(nodo, list):
            continue
        tipo = nodo[0]
        if tipo in ("gr_line", "gr_rect", "gr_circle", "gr_arc", "gr_poly"):
            capa = str(sx.val(nodo, "layer", ""))
            g = _grafico(nodo, ident, capa)
            if g:
                (modelo["contorno"] if capa == "Edge.Cuts" else modelo["graficos"]).append(g)
                if capa == "Edge.Cuts" and g["t"] == "poly":
                    todos.extend(tuple(p) for p in g.get("pts", []))
        elif tipo == "footprint":
            T = _Transformada(sx.find(nodo, "at"))
            lado = "B" if str(sx.val(nodo, "layer", "F.Cu")) == "B.Cu" else "F"
            props = {}
            prims = []
            for prop in sx.find_all(nodo, "property"):
                props[str(prop[1])] = str(prop[2]) if len(prop) > 2 else ""
                capa = str(sx.val(prop, "layer", ""))
                if str(prop[1]) == "Reference" and not sx.is_yes(prop, "hide") and capa.endswith("SilkS"):
                    at = sx.find(prop, "at")
                    p = T(_xy(at))
                    size = sx.find(sx.find(sx.find(prop, "effects") or [], "font") or [], "size")
                    prims.append({"t": "text", "p": [_r(p[0]), _r(p[1])], "s": str(prop[2]),
                                  "size": float(size[1]) if size else 1.0, "capa": capa, "ang": 0})
            ref = props.get("Reference", "?")
            for g in nodo[1:]:
                if isinstance(g, list) and g and g[0] in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
                    capa = str(sx.val(g, "layer", ""))
                    if capa.endswith(("SilkS", "Fab", "CrtYd")):
                        prim = _grafico(g, T, capa)
                        if prim:
                            prims.append(prim)
            pads = []
            for pad in sx.find_all(nodo, "pad"):
                at = sx.find(pad, "at")
                p = T(_xy(at))
                size = sx.find(pad, "size")
                capas = [str(c) for c in (sx.find(pad, "layers") or [])[1:]]
                drill = sx.find(pad, "drill")
                taladro = 0.0
                if drill is not None and len(drill) > 1:
                    numeros = [float(v) for v in drill[1:] if isinstance(v, (int, float))]
                    taladro = min(numeros) if numeros else 0.0
                forma = str(pad[3]) if len(pad) > 3 else "rect"
                cobre = [c for c in CAPAS_COBRE if "*.Cu" in capas or c in capas or ("F&B.Cu" in capas and c in ("F.Cu", "B.Cu"))]
                info = {"num": str(pad[1]), "red": _red(pad), "tipo": str(pad[2]), "forma": forma,
                        "p": [_r(p[0]), _r(p[1])], "w": _r(size[1]), "h": _r(size[2]),
                        "ang": _r(float(at[3]) if len(at) > 3 else 0.0), "taladro": _r(taladro), "capas": cobre,
                        "rr": _r(float(sx.val(pad, "roundrect_rratio", 0.25))) if forma == "roundrect" else 0.0,
                        "ref": ref}
                pads.append(info)
                pads_global.append(info)
                todos.append(tuple(info["p"]))
            modelo["huellas"].append({"ref": ref, "valor": props.get("Value", ""), "uuid": str(sx.val(nodo, "uuid", "")),
                                      "lado": lado, "at": [_r(T.x), _r(T.y)], "ang": _r(T.ang), "prims": prims, "pads": pads})
        elif tipo == "segment":
            a, b = _xy(sx.find(nodo, "start")), _xy(sx.find(nodo, "end"))
            modelo["pistas"].append({"a": [_r(a[0]), _r(a[1])], "b": [_r(b[0]), _r(b[1])], "w": float(sx.val(nodo, "width", 0.25)),
                                     "capa": str(sx.val(nodo, "layer", "F.Cu")), "red": _red(nodo), "uuid": str(sx.val(nodo, "uuid", ""))})
        elif tipo == "arc":
            s, m, e = (_xy(sx.find(nodo, k)) for k in ("start", "mid", "end"))
            modelo["pistas"].append({"a": [_r(s[0]), _r(s[1])], "b": [_r(e[0]), _r(e[1])], "d": _arc_path(s, m, e),
                                     "w": float(sx.val(nodo, "width", 0.25)), "capa": str(sx.val(nodo, "layer", "F.Cu")),
                                     "red": _red(nodo), "uuid": str(sx.val(nodo, "uuid", ""))})
        elif tipo == "via":
            p = _xy(sx.find(nodo, "at"))
            modelo["vias"].append({"p": [_r(p[0]), _r(p[1])], "d": float(sx.val(nodo, "size", 0.8)),
                                   "taladro": float(sx.val(nodo, "drill", 0.4)), "red": _red(nodo)})
        elif tipo == "zone":
            red = _red(nodo) or str(sx.val(nodo, "net_name", "") or "")
            capas = _capas_de(nodo, CAPAS_COBRE)
            reglas = sx.find(nodo, "keepout")
            conecta = sx.find(nodo, "connect_pads")
            conexion = "termica"
            if conecta is not None and len(conecta) > 1 and not isinstance(conecta[1], list):
                conexion = {"yes": "solida", "no": "ninguna", "thru_hole_only": "solo_tht"}.get(str(conecta[1]), "termica")
            contornos = []
            for poly in sx.find_all(nodo, "polygon"):
                pts = [_xy(q) for q in sx.find_all(sx.find(poly, "pts") or [], "xy")]
                if len(pts) >= 3:
                    contornos.append([[_r(x), _r(y)] for x, y in pts])
            indice = len(modelo["areas"])
            relleno = 0.0
            for fpoly in sx.find_all(nodo, "filled_polygon"):
                pts = [_xy(q) for q in sx.find_all(sx.find(fpoly, "pts") or [], "xy")]
                if len(pts) < 3:
                    continue
                relleno += _area_poligono(pts)
                modelo["zonas"].append({"capa": str(sx.val(fpoly, "layer", capas[0] if capas else "")), "red": red,
                                        "area": indice, "pts": [[_r(x), _r(y)] for x, y in pts]})
            modelo["areas"].append({
                "indice": indice, "nombre": str(sx.val(nodo, "name", "") or ""), "red": red, "capas": capas,
                "prioridad": int(sx.val(nodo, "priority", 0) or 0), "conexion": conexion, "contornos": contornos,
                "rellena": relleno > 0, "area_mm2": round(relleno, 1), "uuid": str(sx.val(nodo, "uuid", "")),
                "regla": ({str(k[0]): str(k[1]) for k in reglas[1:] if isinstance(k, list) and len(k) > 1}
                          if reglas is not None else None)})

    cobre_conexion: dict = {}
    for z in modelo["zonas"]:
        cobre_conexion.setdefault((z["area"], z["capa"]), {"capa": z["capa"], "red": z["red"], "polys": []})["polys"].append(z["pts"])
    for a in modelo["areas"]:
        if a["regla"] is None and not a["rellena"] and a["red"]:
            for capa in a["capas"]:
                cobre_conexion[(a["indice"], capa)] = {"capa": capa, "red": a["red"], "polys": a["contornos"]}
    modelo["ratsnest"] = _ratsnest(pads_global, modelo["pistas"], modelo["vias"], list(cobre_conexion.values()))
    if todos:
        xs, ys = [p[0] for p in todos], [p[1] for p in todos]
        modelo["limites"] = [_r(min(xs)), _r(min(ys)), _r(max(xs)), _r(max(ys))]
    else:
        modelo["limites"] = [0, 0, 100, 60]
    redes = {p["red"] for p in pads_global if p["red"]}
    modelo["resumen"] = {"huellas": len(modelo["huellas"]), "pistas": len(modelo["pistas"]), "vias": len(modelo["vias"]),
                         "redes": len(redes), "sin_rutear": len(modelo["ratsnest"]),
                         "zonas": sum(1 for a in modelo["areas"] if a["regla"] is None),
                         "prohibidas": sum(1 for a in modelo["areas"] if a["regla"] is not None),
                         "abajo": sum(1 for h in modelo["huellas"] if h["lado"] == "B")}
    return modelo


def _ratsnest(pads, pistas, vias, zonas) -> list:
    padre = {}

    def raiz(k):
        while padre.setdefault(k, k) != k:
            padre[k] = padre[padre[k]]
            k = padre[k]
        return k

    def unir(a, b):
        padre[raiz(a)] = raiz(b)

    por_red: dict[str, dict] = {}
    for i, p in enumerate(pads):
        if p["red"]:
            por_red.setdefault(p["red"], {"pads": [], "pistas": [], "vias": [], "zonas": []})["pads"].append(i)
    for nombre, lista in (("pistas", pistas), ("vias", vias), ("zonas", zonas)):
        for i, item in enumerate(lista):
            if item["red"] in por_red:
                por_red[item["red"]][nombre].append(i)

    lineas = []
    for red, items in por_red.items():
        extremos = []
        for i in items["pistas"]:
            t = pistas[i]
            extremos.append((("pista", i), t["a"], t["capa"]))
            extremos.append((("pista", i), t["b"], t["capa"]))
        for (ka, pa, ca) in extremos:
            for j in items["pads"]:
                pad = pads[j]
                if ca in pad["capas"] and _pad_dentro(pad, pa[0], pa[1], 0.01):
                    unir(ka, ("pad", j))
            for j in items["pistas"]:
                t = pistas[j]
                if ("pista", j) != ka and t["capa"] == ca and _dist_segmento(pa[0], pa[1], t["a"], t["b"]) <= t["w"] / 2 + 0.01:
                    unir(ka, ("pista", j))
            for j in items["vias"]:
                v = vias[j]
                if math.hypot(pa[0] - v["p"][0], pa[1] - v["p"][1]) <= v["d"] / 2 + 0.01:
                    unir(ka, ("via", j))
        for j in items["vias"]:
            v = vias[j]
            for k in items["pads"]:
                if _pad_dentro(pads[k], v["p"][0], v["p"][1]):
                    unir(("via", j), ("pad", k))
        for z in items["zonas"]:
            zona = zonas[z]
            raster = _Raster(zona["polys"])
            for k in items["pads"]:
                pad = pads[k]
                if zona["capa"] in pad["capas"] and _toca_pad(raster, pad):
                    unir(("zona", z), ("pad", k))
            for j in items["vias"]:
                if _toca_circulo(raster, vias[j]["p"], vias[j]["d"] / 2):
                    unir(("zona", z), ("via", j))
            for (ka, pa, ca) in extremos:
                if ca == zona["capa"] and raster.dentro(pa[0], pa[1]):
                    unir(("zona", z), ka)
        grupos: dict = {}
        for k in items["pads"]:
            grupos.setdefault(raiz(("pad", k)), []).append(pads[k])
        grupos = list(grupos.values())
        if len(grupos) < 2:
            continue

        def par_cercano(a, b):
            return min(((math.hypot(p["p"][0] - q["p"][0], p["p"][1] - q["p"][1]), p, q) for p in grupos[a] for q in grupos[b]),
                       key=lambda t: t[0])

        if len(grupos) <= 40:
            aristas = []
            for a in range(len(grupos)):
                for b in range(a + 1, len(grupos)):
                    d, p, q = par_cercano(a, b)
                    aristas.append((d, a, b))
        else:
            centros = [(sum(p["p"][0] for p in g) / len(g), sum(p["p"][1] for p in g) / len(g)) for g in grupos]
            aristas = [(math.dist(centros[a], centros[b]), a, b) for a in range(len(grupos)) for b in range(a + 1, len(grupos))]
        aristas.sort(key=lambda t: t[0])
        conjunto = list(range(len(grupos)))

        def buscar(k):
            while conjunto[k] != k:
                conjunto[k] = conjunto[conjunto[k]]
                k = conjunto[k]
            return k

        for _, a, b in aristas:
            ra, rb = buscar(a), buscar(b)
            if ra != rb:
                conjunto[ra] = rb
                _, p, q = par_cercano(a, b)
                lineas.append({"a": p["p"], "b": q["p"], "red": red})
    return lineas
