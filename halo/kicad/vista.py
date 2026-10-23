"""Convierte un .kicad_sch en una lista de dibujo para el Halo.

Coordenadas de hoja en milímetros, Y hacia abajo, ya transformadas: la interfaz sólo
dibuja. Cubre símbolos (cuerpo, pines, campos), cables, buses, uniones, etiquetas,
marcas de no conexión y textos sueltos.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import sexpr as sx
from .geometria import Transform, place, transform

PAPEL = {"A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594),
         "A0": (1189, 841), "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8),
         "D": (863.6, 558.8), "E": (1117.6, 863.6), "USLetter": (279.4, 215.9),
         "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4)}


def _r(v: float) -> float:
    return round(float(v), 4)


def _pt(p) -> list[float]:
    return [_r(p[0]), _r(p[1])]


def _xy(node) -> tuple[float, float]:
    return (float(node[1]), float(node[2]))


def _fill(node) -> str:
    fill = sx.find(node, "fill")
    return str(sx.val(fill, "type", "none")) if fill else "none"


def _width(node) -> float:
    stroke = sx.find(node, "stroke")
    return float(sx.val(stroke, "width", 0) or 0) if stroke else 0.0


def _effects(node):
    size, jh, jv = 1.27, "center", "center"
    effects = sx.find(node, "effects")
    hidden = sx.is_yes(node, "hide")
    if effects:
        font = sx.find(effects, "font")
        if font and sx.find(font, "size"):
            size = float(sx.find(font, "size")[1])
        justify = sx.find(effects, "justify")
        if justify:
            for a in justify[1:]:
                if a in ("left", "right"):
                    jh = str(a)
                elif a in ("top", "bottom"):
                    jv = str(a)
        hidden = hidden or sx.is_yes(effects, "hide")
    return size, jh, jv, hidden


def _text(p, s, size, ang=0, jh="center", jv="center", cls="texto"):
    return {"t": "text", "p": _pt(p), "s": s, "size": _r(size), "ang": int(ang) % 360, "jh": jh, "jv": jv, "c": cls}


def _arc_path(s, m, e) -> str | None:
    ax, ay = s
    bx, by = m
    cx, cy = e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return f"M {ax:.4f} {ay:.4f} L {cx:.4f} {cy:.4f}"
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    sweep = 1 if cross > 0 else 0
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(cy - uy, cx - ux)
    span = (a1 - a0) % (2 * math.pi) if sweep else (a0 - a1) % (2 * math.pi)
    large = 1 if span > math.pi else 0
    return f"M {ax:.4f} {ay:.4f} A {radius:.4f} {radius:.4f} 0 {large} {sweep} {cx:.4f} {cy:.4f}"


def _unit_parts(libsym, unit: int, style: int):
    for sub in sx.find_all(libsym, "symbol"):
        m = re.match(r".*_(\d+)_(\d+)$", str(sub[1]))
        if not m:
            continue
        u, st = int(m.group(1)), int(m.group(2))
        if u in (0, unit) and st in (0, style):
            yield sub


def _pin_geometry(pin):
    at = sx.find(pin, "at")
    px, py = float(at[1]), float(at[2])
    ang = float(at[3]) if len(at) > 3 else 0.0
    length = float(sx.val(pin, "length", 2.54))
    rad = math.radians(ang)
    return (px, py), (px + length * math.cos(rad), py + length * math.sin(rad)), length


def symbol_prims(libsym, at, t: Transform, unit: int = 1, style: int = 1):
    """Primitivas del cuerpo y los pines de un símbolo colocado."""
    prims, points = [], []
    pin_names = sx.find(libsym, "pin_names")
    names_hidden = sx.is_yes(pin_names, "hide") if pin_names else False
    name_offset = float(sx.val(pin_names, "offset", 0.508)) if pin_names else 0.508
    numbers_hidden = sx.is_yes(sx.find(libsym, "pin_numbers"), "hide") if sx.find(libsym, "pin_numbers") else False

    def P(p):
        q = place(at, t, p)
        points.append(q)
        return q

    for part in _unit_parts(libsym, unit, style):
        for g in part[1:]:
            if not isinstance(g, list):
                continue
            kind = g[0]
            if kind == "polyline" or kind == "bezier":
                pts = [P(_xy(p)) for p in sx.find_all(sx.find(g, "pts"), "xy")]
                prims.append({"t": "poly", "pts": [_pt(p) for p in pts], "fill": _fill(g), "w": _width(g)})
            elif kind == "rectangle":
                (x0, y0), (x1, y1) = _xy(sx.find(g, "start")), _xy(sx.find(g, "end"))
                pts = [P((x0, y0)), P((x1, y0)), P((x1, y1)), P((x0, y1)), P((x0, y0))]
                prims.append({"t": "poly", "pts": [_pt(p) for p in pts], "fill": _fill(g), "w": _width(g), "closed": True})
            elif kind == "circle":
                c = P(_xy(sx.find(g, "center")))
                radius = float(sx.val(g, "radius", 0))
                points.extend([(c[0] - radius, c[1] - radius), (c[0] + radius, c[1] + radius)])
                prims.append({"t": "circle", "c": _pt(c), "r": _r(radius), "fill": _fill(g), "w": _width(g)})
            elif kind == "arc":
                s, m, e = (P(_xy(sx.find(g, k))) for k in ("start", "mid", "end"))
                d = _arc_path(s, m, e)
                if d:
                    prims.append({"t": "path", "d": d, "fill": _fill(g), "w": _width(g)})
            elif kind == "text":
                size, jh, jv, hidden = _effects(g)
                if hidden:
                    continue
                tat = sx.find(g, "at")
                ang = (float(tat[3]) if len(tat) > 3 else 0) / 10 if float(tat[3] if len(tat) > 3 else 0) > 360 else (float(tat[3]) if len(tat) > 3 else 0)
                if t.swaps_axes:
                    ang = 90 if int(ang) % 180 == 0 else 0
                prims.append(_text(P(_xy(tat)), str(g[1]), size, ang, jh, jv, "simbolo"))
            elif kind == "pin":
                if sx.is_yes(g, "hide"):
                    continue
                start, end, length = _pin_geometry(g)
                a, b = P(start), P(end)
                shape = str(g[2]) if len(g) > 2 else "line"
                prims.append({"t": "pin", "a": _pt(a), "b": _pt(b), "forma": shape})
                dx, dy = (b[0] - a[0], b[1] - a[1])
                norm = math.hypot(dx, dy) or 1.0
                ux, uy = dx / norm, dy / norm
                vertical = abs(uy) > abs(ux)
                name = str(sx.val(g, "name", ""))
                number = str(sx.val(g, "number", ""))
                name_node = sx.find(g, "name")
                nsize = _effects(name_node)[0] if name_node else 1.27
                if not numbers_hidden and number and length > 0:
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    if vertical:
                        prims.append(_text((mid[0] - 0.35, mid[1]), number, nsize * 0.8, 90, "center", "bottom", "pin-num"))
                    else:
                        prims.append(_text((mid[0], mid[1] - 0.35), number, nsize * 0.8, 0, "center", "bottom", "pin-num"))
                if not names_hidden and name and name != "~":
                    if name_offset > 0:
                        p = (b[0] + ux * name_offset, b[1] + uy * name_offset)
                        if vertical:
                            prims.append(_text(p, name, nsize, 90, "left" if uy < 0 else "right", "center", "pin-nombre"))
                        else:
                            prims.append(_text(p, name, nsize, 0, "left" if ux > 0 else "right", "center", "pin-nombre"))
                    else:
                        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                        if vertical:
                            prims.append(_text((mid[0] - 0.35, mid[1]), name, nsize, 90, "center", "bottom", "pin-nombre"))
                        else:
                            prims.append(_text((mid[0], mid[1] - 0.35), name, nsize, 0, "center", "bottom", "pin-nombre"))
    return prims, points


def _bbox(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [_r(min(xs)), _r(min(ys)), _r(max(xs)), _r(max(ys))]


def cargar_modelo(path: Path | str) -> dict:
    path = Path(path)
    root = sx.loads(path.read_text(encoding="utf-8"))
    if not root or root[0] != "kicad_sch":
        raise ValueError(f"{path} no es un esquemático de KiCad")
    paper = str(sx.val(root, "paper", "A4"))
    size = PAPEL.get(paper, PAPEL["A4"])
    if paper == "User":
        node = sx.find(root, "paper")
        size = (float(node[2]), float(node[3]))
    else:
        node = sx.find(root, "paper")
        if node and len(node) > 2 and node[2] == "portrait":
            size = (size[1], size[0])
    title_block = sx.find(root, "title_block")
    library = {str(s[1]): s for s in sx.find_all(sx.find(root, "lib_symbols"), "symbol")}

    model = {"tipo": "esquematico", "archivo": str(path), "nombre": path.stem, "papel": [paper, *size],
             "titulo": str(sx.val(title_block, "title", "")) if title_block else "",
             "simbolos": [], "cables": [], "buses": [], "uniones": [], "etiquetas": [], "sin_conexion": [], "textos": []}
    everything = []

    for node in sx.find_all(root, "symbol"):
        lib_id = str(sx.val(node, "lib_id", ""))
        at_node = sx.find(node, "at")
        at = (float(at_node[1]), float(at_node[2]))
        rotation = float(at_node[3]) if len(at_node) > 3 else 0
        mirror = sx.val(node, "mirror")
        t = transform(rotation, str(mirror) if mirror else None)
        unit = int(sx.val(node, "unit", 1))
        style = int(sx.val(node, "body_style", sx.val(node, "convert", 1)) or 1)
        libsym = library.get(lib_id)
        prims, points = symbol_prims(libsym, at, t, unit, style) if libsym is not None else ([], [at])
        props = {}
        for prop in sx.find_all(node, "property"):
            key, value = str(prop[1]), str(prop[2])
            props[key] = value
            size_, jh, jv, hidden = _effects(prop)
            if hidden or not value or key not in ("Reference", "Value") and not value:
                continue
            if key not in ("Reference", "Value"):
                continue
            pat = sx.find(prop, "at")
            ang = float(pat[3]) if len(pat) > 3 else 0
            if t.swaps_axes:
                ang = 90 if int(ang) % 180 == 0 else 0
            cls = "ref" if key == "Reference" else "valor"
            prims.append(_text(_xy(pat), value, size_, ang, jh, jv, cls))
            prims[-1]["campo"] = key
            prims[-1]["uuid"] = str(sx.val(node, "uuid", ""))
        bbox = _bbox(points)
        everything.extend(points)
        model["simbolos"].append({"uuid": str(sx.val(node, "uuid", "")), "lib_id": lib_id,
                                  "unidad": unit,
                                  "ref": props.get("Reference", "?"), "valor": props.get("Value", ""),
                                  "at": _pt(at), "rot": int(rotation), "bbox": bbox,
                                  "power": lib_id.startswith("power:") or props.get("Reference", "").startswith("#"),
                                  "prims": prims})

    for kind, key in (("wire", "cables"), ("bus", "buses")):
        for node in sx.find_all(root, kind):
            pts = [_xy(p) for p in sx.find_all(sx.find(node, "pts"), "xy")]
            everything.extend(pts)
            model[key].append({"uuid": str(sx.val(node, "uuid", "")), "pts": [_pt(p) for p in pts]})
    for node in sx.find_all(root, "junction"):
        model["uniones"].append(_pt(_xy(sx.find(node, "at"))))
    for node in sx.find_all(root, "no_connect"):
        model["sin_conexion"].append(_pt(_xy(sx.find(node, "at"))))
    for kind in ("label", "global_label", "hierarchical_label"):
        for node in sx.find_all(root, kind):
            pat = sx.find(node, "at")
            size_, jh, jv, _ = _effects(node)
            p = _xy(pat)
            everything.append(p)
            model["etiquetas"].append({"texto": str(node[1]), "p": _pt(p), "ang": int(float(pat[3]) if len(pat) > 3 else 0),
                                       "tipo": {"label": "local", "global_label": "global"}.get(kind, "jerarquica"),
                                       "size": _r(size_), "jh": jh, "jv": jv if kind == "label" else "center",
                                       "uuid": str(sx.val(node, "uuid", "")), "forma": str(sx.val(node, "shape", "passive"))})
    for node in sx.find_all(root, "text"):
        pat = sx.find(node, "at")
        size_, jh, jv, hidden = _effects(node)
        if not hidden:
            t = _text(_xy(pat), str(node[1]), size_, float(pat[3]) if len(pat) > 3 else 0, jh, jv, "nota")
            fuente = sx.find(sx.find(node, "effects") or [], "font")
            t["negrita"] = bool(fuente and sx.is_yes(fuente, "bold"))
            model["textos"].append(t)
            everything.append(_xy(pat))
    model["bloques"] = []
    for node in sx.find_all(root, "rectangle"):
        a, b = _xy(sx.find(node, "start")), _xy(sx.find(node, "end"))
        stroke = sx.find(node, "stroke")
        model["bloques"].append({"a": _pt(a), "b": _pt(b), "uuid": str(sx.val(node, "uuid", "")),
                                 "discontinuo": str(sx.val(stroke, "type", "")) in ("dash", "dash_dot", "dot") if stroke else False})
        everything.extend([a, b])
    for bloque in model["bloques"]:
        x0, y0 = min(bloque["a"][0], bloque["b"][0]), min(bloque["a"][1], bloque["b"][1])
        titulo = next((t for t in model["textos"] if abs(t["p"][0] - x0) < 0.02 and abs(t["p"][1] - (y0 - 1.27)) < 0.02), None)
        bloque["titulo"] = titulo["s"] if titulo else ""
        bloque["titulo_size"] = titulo["size"] if titulo else 2.0
        if titulo:
            model["textos"].remove(titulo)

    model["limites"] = _bbox(everything) or [0, 0, size[0], size[1]]
    real = [s for s in model["simbolos"] if not s["power"]]
    model["resumen"] = {"componentes": len(real), "simbolos_potencia": len(model["simbolos"]) - len(real),
                        "cables": len(model["cables"]), "uniones": len(model["uniones"]),
                        "etiquetas": len(model["etiquetas"])}
    return model
