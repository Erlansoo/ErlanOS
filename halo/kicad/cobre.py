"""Cobre por zonas, como en una placa hecha a mano en KiCad.

  cobre     zona de una red en una o varias capas: toda la placa, un rectángulo, un polígono o
            alrededor de ciertas piezas. Con prioridad (donde se solapan gana la mayor: planos
            divididos como +24V / +3V3 / +5V en una misma capa), conexión térmica o sólida y holgura
  plano     capa entera (o dividida) de una o más redes; esa capa deja de usarse para pistas
  prohibir  área de reglas: sin pistas, vías, cobre o huellas (bajo antenas, agujeros, aislamientos)
  rellenar  KiCad calcula el cobre (ZONE_FILLER) para que se vea y conecte ya, no sólo al verificar
  costura   vías de una red cosiendo su cobre de varias capas, en rejilla
  bajar     cada pad SMD de una red de plano baja a su plano por una vía corta junto al pad
"""
from __future__ import annotations

import math

import pcbnew

from .vista_pcb import _Raster

CONEXION = {"termica": pcbnew.ZONE_CONNECTION_THERMAL, "solida": pcbnew.ZONE_CONNECTION_FULL,
            "ninguna": pcbnew.ZONE_CONNECTION_NONE}
NOMBRE_CONEXION = {v: k for k, v in CONEXION.items()}


def _mm(v) -> float:
    return pcbnew.ToMM(v)


def _cadenas(poly) -> list[list[tuple[float, float]]]:
    """Contornos y agujeros de un SHAPE_POLY_SET, en mm."""
    salida = []
    for i in range(poly.OutlineCount()):
        cadena = poly.COutline(i)
        salida.append([(_mm(cadena.CPoint(k).x), _mm(cadena.CPoint(k).y)) for k in range(cadena.PointCount())])
        for h in range(poly.HoleCount(i)):
            hueco = poly.CHole(i, h)
            salida.append([(_mm(hueco.CPoint(k).x), _mm(hueco.CPoint(k).y)) for k in range(hueco.PointCount())])
    return salida


def puntos_contorno(zona) -> list[list[tuple[float, float]]]:
    return _cadenas(zona.Outline())


def area_rellena(zona) -> float:
    return sum(zona.GetFilledPolysList(l).Area() / 1e12 for l in zona.GetLayerSet().Seq() if zona.HasFilledPolysForLayer(l))


def region_puntos(placa, region) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = placa.contorno_mm
    m = placa.margen_borde

    def recorte(a, b, c, d):
        a, c = sorted((a, c))
        b, d = sorted((b, d))
        a, b, c, d = max(a, x0 + m), max(b, y0 + m), min(c, x1 - m), min(d, y1 - m)
        if c - a < 0.5 or d - b < 0.5:
            raise ValueError("la región queda fuera de la placa")
        return [(a, b), (c, b), (c, d), (a, d)]

    if region in (None, "", "placa"):
        return recorte(x0, y0, x1, y1)
    if isinstance(region, str) and region.startswith("cerca:"):
        refs = [r.strip() for r in region[6:].split(",") if r.strip()]
        faltan = [r for r in refs if r not in placa.huellas]
        if faltan or not refs:
            raise KeyError(f"no existen las huellas {', '.join(faltan) or '(ninguna)'}")
        cajas = [placa._patio(placa.huellas[r]) for r in refs]
        return recorte(min(c[0] for c in cajas) - 2, min(c[1] for c in cajas) - 2,
                       max(c[2] for c in cajas) + 2, max(c[3] for c in cajas) + 2)
    numeros = [float(v) for v in region.replace(";", ",").split(",")] if isinstance(region, str) else list(region)
    if len(numeros) == 4 and all(isinstance(v, (int, float)) for v in numeros):
        return recorte(*numeros)
    if isinstance(region, str):
        if len(numeros) < 6 or len(numeros) % 2:
            raise ValueError("región: placa, x0,y0,x1,y1, cerca:REF1,REF2 o una lista x,y,x,y,... de 3 o más vértices")
        return [(numeros[i], numeros[i + 1]) for i in range(0, len(numeros), 2)]
    return [tuple(p) for p in region]


def _capas(placa, capas) -> list[int]:
    if capas in ("todas", ["todas"]):
        return list(placa.pila)
    return [placa.capa_id(c) for c in capas]


def cobre(placa, red: str, capas, region=None, prioridad: int = 0, conexion: str = "termica",
          holgura: float | None = None, ancho_min: float = 0.25, termico=(0.5, 0.5), nombre: str = "",
          plano: bool = False):
    net = placa.board.FindNet(red)
    if net is None or not red:
        raise KeyError(f"no existe la red {red}")
    if conexion not in CONEXION:
        raise ValueError(f"conexión {conexion}: usa termica, solida o ninguna")
    ids = _capas(placa, capas)
    zona = pcbnew.ZONE(placa.board)
    conjunto = pcbnew.LSET()
    for lid in ids:
        conjunto.AddLayer(lid)
    zona.SetLayerSet(conjunto)
    zona.SetNetCode(net.GetNetCode())
    zona.SetAssignedPriority(int(prioridad))
    if nombre:
        zona.SetZoneName(nombre)
    zona.SetPadConnection(CONEXION[conexion])
    zona.SetLocalClearance(pcbnew.FromMM(holgura if holgura is not None else max(placa.regla(red).holgura, 0.3)))
    zona.SetMinThickness(pcbnew.FromMM(ancho_min))
    zona.SetThermalReliefGap(pcbnew.FromMM(termico[0]))
    zona.SetThermalReliefSpokeWidth(pcbnew.FromMM(termico[1]))
    zona.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    contorno = zona.Outline()
    contorno.NewOutline()
    for x, y in region_puntos(placa, region):
        contorno.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    placa.board.Add(zona)
    if plano:
        for lid in ids:
            lista = placa.planos.setdefault(pcbnew.LayerName(lid), [])
            if red not in lista:
                lista.append(red)
    return zona


def prohibir(placa, capas, region, sin=("pistas", "vias", "cobre"), nombre: str = ""):
    zona = pcbnew.ZONE(placa.board)
    zona.SetIsRuleArea(True)
    conjunto = pcbnew.LSET()
    for lid in _capas(placa, capas):
        conjunto.AddLayer(lid)
    zona.SetLayerSet(conjunto)
    if nombre:
        zona.SetZoneName(nombre)
    zona.SetDoNotAllowTracks("pistas" in sin)
    zona.SetDoNotAllowVias("vias" in sin)
    zona.SetDoNotAllowPads("pads" in sin)
    zona.SetDoNotAllowFootprints("huellas" in sin)
    if hasattr(zona, "SetDoNotAllowZoneFills"):
        zona.SetDoNotAllowZoneFills("cobre" in sin)
    else:
        zona.SetDoNotAllowCopperPour("cobre" in sin)
    contorno = zona.Outline()
    contorno.NewOutline()
    for x, y in region_puntos(placa, region):
        contorno.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
    placa.board.Add(zona)
    return zona


def rellenar(placa) -> bool:
    placa._rellenos = None
    return pcbnew.ZONE_FILLER(placa.board).Fill(placa.board.Zones())


def _regla_area(zona) -> dict:
    cobre_no = zona.GetDoNotAllowZoneFills() if hasattr(zona, "GetDoNotAllowZoneFills") else zona.GetDoNotAllowCopperPour()
    return {"pistas": zona.GetDoNotAllowTracks(), "vias": zona.GetDoNotAllowVias(), "cobre": cobre_no,
            "pads": zona.GetDoNotAllowPads(), "huellas": zona.GetDoNotAllowFootprints()}


def zonas(placa) -> list[dict]:
    salida = []
    for z in placa.board.Zones():
        caja = z.GetBoundingBox()
        capas = [pcbnew.LayerName(l) for l in z.GetLayerSet().Seq() if l in placa.pila]
        salida.append({
            "nombre": z.GetZoneName(), "red": z.GetNetname(), "capas": capas, "prioridad": z.GetAssignedPriority(),
            "regla": _regla_area(z) if z.GetIsRuleArea() else None,
            "conexion": NOMBRE_CONEXION.get(z.GetPadConnection(), str(z.GetPadConnection())),
            "area_mm2": round(area_rellena(z), 1) if not z.GetIsRuleArea() else 0.0,
            "caja": [round(_mm(caja.GetX()), 2), round(_mm(caja.GetY()), 2),
                     round(_mm(caja.GetX() + caja.GetWidth()), 2), round(_mm(caja.GetY() + caja.GetHeight()), 2)]})
    return salida


def quitar(placa, nombre=None, red=None, capa=None) -> int:
    lid = placa.capa_id(capa) if capa else None
    n = 0
    for z in list(placa.board.Zones()):
        if nombre and z.GetZoneName() != nombre:
            continue
        if red and z.GetNetname() != red:
            continue
        if lid is not None and not z.GetLayerSet().Contains(lid):
            continue
        placa.board.Delete(z)
        n += 1
    for nombre_capa in list(placa.planos):
        lid_plano = placa.capa_id(nombre_capa)
        vivas = {z.GetNetname() for z in placa.board.Zones() if not z.GetIsRuleArea() and z.GetLayerSet().Contains(lid_plano)}
        placa.planos[nombre_capa] = [r for r in placa.planos[nombre_capa] if r in vivas]
        if not placa.planos[nombre_capa]:
            del placa.planos[nombre_capa]
    return n


def areas_prohibidas(placa) -> list[dict]:
    """Áreas de reglas que afectan al enrutador: dónde no pueden ir pistas o vías."""
    salida = []
    for z in placa.board.Zones():
        if not z.GetIsRuleArea() or not (z.GetDoNotAllowTracks() or z.GetDoNotAllowVias()):
            continue
        cadenas = puntos_contorno(z)
        todos = [p for c in cadenas for p in c]
        if not todos:
            continue
        salida.append({"capas": [placa.pila.index(l) for l in z.GetLayerSet().Seq() if l in placa.pila],
                       "pistas": z.GetDoNotAllowTracks(), "vias": z.GetDoNotAllowVias(), "raster": _Raster(cadenas),
                       "caja": (min(p[0] for p in todos), min(p[1] for p in todos), max(p[0] for p in todos), max(p[1] for p in todos))})
    return salida


def cajas_sin_huellas(placa) -> list[tuple[tuple, tuple]]:
    salida = []
    for z in placa.board.Zones():
        if z.GetIsRuleArea() and z.GetDoNotAllowFootprints():
            b = z.GetBoundingBox()
            lados = tuple(l for l, lid in (("F", pcbnew.F_Cu), ("B", pcbnew.B_Cu)) if z.GetLayerSet().Contains(lid)) or ("F", "B")
            salida.append(((_mm(b.GetX()), _mm(b.GetY()), _mm(b.GetX() + b.GetWidth()), _mm(b.GetY() + b.GetHeight())), lados))
    return salida


def _disco_dentro(raster: _Raster, x: float, y: float, r: float) -> bool:
    return raster.dentro(x, y) and all(raster.dentro(x + r * math.cos(k * math.pi / 4), y + r * math.sin(k * math.pi / 4))
                                       for k in range(8))


def costura(placa, red: str, paso: float = 5.0) -> int:
    rellenar(placa)
    rellenos: dict[int, list] = {}
    for z in placa.board.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != red:
            continue
        for lid in z.GetLayerSet().Seq():
            if lid in placa.pila and z.HasFilledPolysForLayer(lid):
                rellenos.setdefault(lid, []).extend(_cadenas(z.GetFilledPolysList(lid)))
    if len(rellenos) < 2:
        raise ValueError(f"{red} necesita cobre relleno en al menos dos capas para coserlas")
    rasters = [_Raster(polys) for polys in rellenos.values()]
    regla = placa.regla(red)
    r = regla.via_d / 2 + 0.05
    propios = [f for f in placa.formas() if f.red == red]
    usadas = [(v["x"], v["y"]) for v in placa.vias]
    x0, y0, x1, y1 = placa.contorno_mm
    puestas = 0
    y = y0 + paso / 2
    while y < y1:
        x = x0 + paso / 2
        while x < x1:
            if (all(_disco_dentro(ra, x, y, r) for ra in rasters)
                    and all(f.distancia(x, y) > r + 0.25 for f in propios)
                    and all(math.hypot(x - ux, y - uy) > paso * 0.5 for ux, uy in usadas)):
                placa._via(round(x, 4), round(y, 4), red)
                usadas.append((x, y))
                puestas += 1
            x += paso
        y += paso
    rellenar(placa)
    return puestas


def bajar_a_plano(placa, red: str, radio: float = 2.5) -> tuple[int, list[str]]:
    """Vía corta junto a cada pad SMD de la red que no está en ninguna capa donde la red tiene cobre."""
    from .placa import G, Rejilla
    propias, ajenas = {}, {}
    for z in placa.board.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().Seq():
            if lid not in placa.pila:
                continue
            if z.GetNetname() == red:
                propias.setdefault(lid, []).append(z)
            else:
                ajenas.setdefault(lid, []).append(z)
    if not propias:
        raise ValueError(f"{red} no tiene cobre en ninguna capa: primero crea su plano o zona")
    indices_propios = {placa.pila.index(l) for l in propias}
    regla = placa.regla(red)
    margen = regla.via_d / 2 + regla.holgura
    pruebas = []
    for lid, lista in propias.items():
        mia = _Raster([c for z in lista for c in puntos_contorno(z)])
        prioridad = min(z.GetAssignedPriority() for z in lista)
        encima = [_Raster(puntos_contorno(z)) for z in ajenas.get(lid, []) if z.GetAssignedPriority() > prioridad]
        pruebas.append((mia, encima))

    def cabe(x, y) -> bool:
        for mia, encima in pruebas:
            if not _disco_dentro(mia, x, y, margen):
                return False
            if any(ra.dentro(x, y) or not all(not ra.dentro(x + margen * math.cos(k * math.pi / 4), y + margen * math.sin(k * math.pi / 4))
                                             for k in range(8)) for ra in encima):
                return False
        return True

    rejillas: dict[int, Rejilla] = {}
    hechas, fallidas, usadas = 0, [], []
    for f in placa.formas():
        if f.red != red or f.pasante or set(f.capas) & indices_propios:
            continue
        if any(p["red"] == red and (f.distancia(*p["a"]) <= 0.02 or f.distancia(*p["b"]) <= 0.02) for p in placa.pistas):
            continue
        capa = f.capas[0]
        if capa not in rejillas:
            rejillas[capa] = Rejilla(placa, red, regla.ancho)
            rejillas[capa].ruteo = {capa}
        rej = rejillas[capa]
        ci, cj = round(f.cx / G), round(f.cy / G)
        n = int(radio / G)
        objetivos = set()
        for i in range(ci - n, ci + n + 1):
            for j in range(cj - n, cj + n + 1):
                x, y = i * G, j * G
                if (math.hypot(x - f.cx, y - f.cy) > radio or (i, j) in rej.via or not rej.libre(i, j, capa)
                        or f.distancia(x, y) < margen or any(math.hypot(x - ux, y - uy) < regla.via_d + regla.holgura for ux, uy in usadas)
                        or not cabe(x, y)):
                    continue
                objetivos.add((i, j, capa))
        camino = placa._buscar(rej, [(ci, cj, capa)], objetivos) if objetivos else None
        if camino is None:
            fallidas.append(f"{f.ref}.{f.numero}")
            continue
        placa._dibujar(red, regla.ancho, (f.cx, f.cy), camino, None)
        xv, yv = round(camino[-1][0] * G, 4), round(camino[-1][1] * G, 4)
        placa._via(xv, yv, red)
        usadas.append((xv, yv))
        hechas += 1
    return hechas, fallidas
