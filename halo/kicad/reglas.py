"""Revisión del dibujo: lo que el ERC no ve pero hace ilegible o engañoso un esquemático."""
from __future__ import annotations

from .ruteo import Mapa, celda, celdas_segmento


def revisar(esquema) -> dict:
    problemas: list[str] = []
    simbolos = list(esquema.simbolos.values())
    for i, a in enumerate(simbolos):
        for b in simbolos[i + 1:]:
            ca, cb = esquema.caja(a), esquema.caja(b)
            if ca[0] < cb[2] and cb[0] < ca[2] and ca[1] < cb[3] and cb[1] < ca[3]:
                problemas.append(f"{a.ref} y {b.ref} se superponen")

    vacio = type("Vacio", (), {})()
    for k, v in vars(esquema).items():
        setattr(vacio, k, v)
    vacio.cables = []
    vacio.caja, vacio.pines_hoja, vacio.area_util = esquema.caja, esquema.pines_hoja, esquema.area_util
    mapa = Mapa(vacio)

    horizontales: dict[tuple, set] = {}
    verticales: dict[tuple, set] = {}
    extremos: dict[tuple, int] = {}
    for w in esquema.cables:
        ca, cb = celda(w["a"]), celda(w["b"])
        celdas = celdas_segmento(ca, cb)
        tabla = horizontales if ca[1] == cb[1] else verticales
        for c in celdas:
            tabla.setdefault(c, set()).add(w["red"])
            if c in mapa.bloq:
                problemas.append(f"un cable de {w['red']} atraviesa {mapa.bloq[c]} en {c}")
            tip = mapa.tips.get(c)
            if tip and tip[2] != w["red"]:
                problemas.append(f"un cable de {w['red']} toca el pin {tip[0]}.{tip[1]} de otra red")
        for c in (ca, cb):
            extremos[c] = extremos.get(c, 0) + 1

    for tabla, eje in ((horizontales, "horizontal"), (verticales, "vertical")):
        for c, redes in tabla.items():
            if len(redes) > 1:
                problemas.append(f"cables de {sorted(redes)} se superponen en {eje} en {c}")

    etiquetas = {celda(e["at"]) for e in esquema.etiquetas}
    for c, n in extremos.items():
        conectado = (c in mapa.tips or n > 1 or c in etiquetas
                     or any(c in t and c not in extremos for t in (horizontales, verticales))
                     or c in horizontales and c in verticales)
        if not conectado:
            problemas.append(f"extremo de cable suelto en {c}")

    cruces = sum(1 for c in horizontales if c in verticales and horizontales[c] != verticales[c] and c not in extremos)
    libres = []
    marcados = {celda(n["at"]) for n in esquema.sin_conexion}
    for s in simbolos:
        for p in esquema.pines_hoja(s):
            clave = (s.ref, p["numero"])
            if clave not in esquema.pin_red and celda(p["tip"]) not in marcados:
                libres.append(f"{s.ref}.{p['numero']}")
    return {"problemas": problemas, "cruces": cruces, "pines_libres": libres}
