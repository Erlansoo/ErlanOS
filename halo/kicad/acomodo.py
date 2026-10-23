"""Acomodo automático con oficio.

Cada pieza busca el lugar de menor costo, en este orden: conectores al borde, la pieza principal de
cada módulo en su centro, el desacoplo junto al pin de alimentación, los cristales junto a los pines
de su integrado, las piezas calientes hacia el borde y luego el resto por afinidad de conexiones.
Después se repasa cada pieza con todas las demás ya puestas.

Qué pesa en el costo:
  conexiones      semiperímetro de cada red; las rápidas pesan 3, las de potencia 1/4 y las que
                  van por plano 1/10
  calor           las calientes van hacia el borde (aire, disipador) y separadas entre sí; las
                  sensibles (electrolíticos, cristales, sensores) lejos de las calientes; alrededor de
                  cada caliente se deja aire para su cobre de disipación
  altura          con --altura-max nada la supera (si puede, baja a la otra cara; si no, se avisa);
                  las piezas altas no se pegan a las de paso fino, para poder soldar y retrabajar
  señales rápidas lejos del borde y del ruido de las piezas de potencia; cristales cortos
  dos caras       con --doble-cara, las piezas aptas (SMD bajas y frías) pueden ir abajo cuando
                  conviene: desacoplo bajo su pin o cuando arriba no hay sitio
  orden           se prefiere 0°; se evita la cara inferior si no aporta
"""
from __future__ import annotations

import math

from . import perfil as pf
from .cobre import cajas_sin_huellas
from .placa import _cruzan, es_potencia, mm, rotar_punto

PASO = 1.27
ANGULOS = (0, 90, 180, 270)


class Indice:
    """Rejilla de cubos para preguntar rápido qué cajas tocan una región."""

    def __init__(self, cubo: float = 5.0):
        self.cubo = cubo
        self.cubos: dict = {}
        self.claves: dict = {}
        self.n = 0

    def _claves(self, caja):
        c = self.cubo
        for gx in range(math.floor(caja[0] / c), math.floor(caja[2] / c) + 1):
            for gy in range(math.floor(caja[1] / c), math.floor(caja[3] / c) + 1):
                yield gx, gy

    def agregar(self, caja, dato) -> int:
        self.n += 1
        item = (self.n, dato, caja)
        claves = list(self._claves(caja))
        for k in claves:
            self.cubos.setdefault(k, []).append(item)
        self.claves[self.n] = claves
        return self.n

    def quitar(self, ident: int) -> None:
        for k in self.claves.pop(ident, []):
            self.cubos[k] = [i for i in self.cubos[k] if i[0] != ident]

    def consultar(self, caja):
        vistos = set()
        for k in self._claves(caja):
            for ident, dato, c in self.cubos.get(k, ()):
                if ident not in vistos:
                    vistos.add(ident)
                    yield dato, c


def _gap(a, b) -> float:
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0.0)


def _caja_girada(caja, ang):
    esquinas = [rotar_punto(px, py, ang) for px, py in ((caja[0], caja[1]), (caja[2], caja[1]), (caja[2], caja[3]), (caja[0], caja[3]))]
    xs, ys = [p[0] for p in esquinas], [p[1] for p in esquinas]
    return (min(xs), min(ys), max(xs), max(ys))


def _rej(v: float) -> float:
    return round(round(v / 0.635) * 0.635, 4)


def _anillo(centro, k):
    if k == 0:
        yield (_rej(centro[0]), _rej(centro[1]))
        return
    for i in range(-k, k + 1):
        for j in ((-k, k) if abs(i) != k else range(-k, k + 1)):
            yield (_rej(centro[0] + i * PASO), _rej(centro[1] + j * PASO))


def es_conector(ref: str, perfil: pf.Perfil) -> bool:
    p = pf.prefijo(ref)
    return p in ("J", "P", "CN") or (p == "X" and not perfil.sensible)


def acomodar(placa, refs=None, separacion: float = 0.8, margen: float = 1.2, doble_cara: bool = False,
             altura_max: float | None = None, altura_abajo: float = 3.0) -> dict:
    if not placa.contorno_mm:
        raise RuntimeError("primero hay que crear la placa")
    x0, y0, x1, y1 = placa.contorno_mm
    perfiles = placa.perfiles(altura_abajo)
    pendientes = [r for r in (refs or sorted(placa.huellas)) if r not in placa.fijadas]
    # Rótulos de serigrafía de la placa (textos sueltos, no de huellas): quedan donde la persona los puso y el
    # acomodo evita taparlos; al final se corren los que aún pisen algo y se avisa de los que no tengan hueco.
    rotulos_fijos = [(t["caja"], t["lado"]) for t in placa.textos_placa()]
    for r in pendientes:
        placa.colocadas.discard(r)
        placa._quitar_pistas_de(r)
    placa._decir(f"Acomodo {len(pendientes)} piezas{' en dos caras' if doble_cara else ''}: miro alturas de los modelos 3D, "
                 f"calor, señales rápidas y paso de pads", "acomodar")

    excedidas, lados_de = [], {}
    for r, fp in placa.huellas.items():
        p = perfiles[r]
        if r not in pendientes:
            lados_de[r] = (placa.lado(fp),)
        elif altura_max and p.altura > altura_max:
            lados_de[r] = ("B",) if doble_cara and p.abajo else ("F",)
            if lados_de[r] == ("F",):
                excedidas.append(r)
        else:
            lados_de[r] = ("F", "B") if doble_cara and p.abajo else ("F",)
    locales = {r: placa._local_lados(placa.huellas[r], lados_de[r]) for r in placa.huellas}

    geo: dict = {}

    def geometria(ref, lado, ang):
        clave = (ref, lado, ang)
        if clave not in geo:
            loc = locales[ref][lado]
            pads = []
            for _num, px, py, red in loc["pads"]:
                if red:
                    dx, dy = rotar_punto(px, py, ang)
                    pads.append((red, dx, dy))
            aire = separacion / 2 + (1.5 if perfiles[ref].calor >= 2 else 0.0)
            c = _caja_girada(loc["caja"], ang)
            geo[clave] = (pads, c, (c[0] - aire, c[1] - aire, c[2] + aire, c[3] + aire))
        return geo[clave]

    def redes_de(ref):
        return {red for _n, _x, _y, red in locales[ref][lados_de[ref][0]]["pads"] if red}

    ocupacion = Indice()
    ident_de, puestos, cajas, pads_red = {}, {}, {}, {}
    grupos = {"calientes": set(), "sensibles": set(), "altos": set(), "finos": set()}
    prohibidas = cajas_sin_huellas(placa)

    def lados_ocupa(ref, lado):
        return ("F", "B") if perfiles[ref].tht else (lado,)

    def poner(ref, pose):
        x, y, ang, lado = pose
        pads, c, cx = geometria(ref, lado, ang)
        puestos[ref] = pose
        cajas[ref] = (x + c[0], y + c[1], x + c[2], y + c[3])
        ident_de[ref] = ocupacion.agregar((x + cx[0], y + cx[1], x + cx[2], y + cx[3]), (ref, lados_ocupa(ref, lado)))
        for red, dx, dy in pads:
            pads_red.setdefault(red, {}).setdefault(ref, []).append((x + dx, y + dy))
        p = perfiles[ref]
        for nombre, cumple in (("calientes", p.calor >= 2), ("sensibles", p.sensible), ("altos", p.altura >= 8.0), ("finos", p.fino)):
            if cumple:
                grupos[nombre].add(ref)

    def retirar(ref):
        pose = puestos.pop(ref)
        ocupacion.quitar(ident_de.pop(ref))
        cajas.pop(ref)
        for lista in pads_red.values():
            lista.pop(ref, None)
        for g in grupos.values():
            g.discard(ref)
        return pose

    for r in placa.colocadas:
        fp = placa.huellas[r]
        poner(r, (mm(fp.GetPosition().x), mm(fp.GetPosition().y), fp.GetOrientationDegrees(), placa.lado(fp)))

    planas, rapidas = placa.redes_plano(), placa.redes_rapidas()

    def peso(red):
        return 0.1 if red in planas else 0.25 if es_potencia(red) else 3.0 if red in rapidas else 1.0

    def valido(ref, x, y, ang, lado) -> bool:
        _, c, cx = geometria(ref, lado, ang)
        real = (x + c[0], y + c[1], x + c[2], y + c[3])
        if real[0] < x0 + margen or real[1] < y0 + margen or real[2] > x1 - margen or real[3] > y1 - margen:
            return False
        caja = (x + cx[0], y + cx[1], x + cx[2], y + cx[3])
        mis = set(lados_ocupa(ref, lado))
        for (otro, lados), oc in ocupacion.consultar(caja):
            if otro != ref and mis & set(lados) and _cruzan(caja, oc):
                return False
        return not any(mis & set(lados) and _cruzan(real, zona) for zona, lados in prohibidas)

    externas: dict = {}

    def preparar(ref):
        externas.clear()
        for red in redes_de(ref):
            pts = [q for o, lista in pads_red.get(red, {}).items() if o != ref for q in lista]
            if pts:
                externas[red] = (min(q[0] for q in pts), min(q[1] for q in pts), max(q[0] for q in pts), max(q[1] for q in pts))

    def costo(ref, x, y, ang, lado, ideal) -> float:
        p = perfiles[ref]
        pads, c, _ = geometria(ref, lado, ang)
        caja = (x + c[0], y + c[1], x + c[2], y + c[3])
        cajas_red: dict = {}
        for red, dx, dy in pads:
            px, py = x + dx, y + dy
            b = cajas_red.get(red)
            cajas_red[red] = (px, py, px, py) if b is None else (min(b[0], px), min(b[1], py), max(b[2], px), max(b[3], py))
        total = 0.0
        for red, b in cajas_red.items():
            e = externas.get(red)
            if e:
                b = (min(b[0], e[0]), min(b[1], e[1]), max(b[2], e[2]), max(b[3], e[3]))
            total += peso(red) * ((b[2] - b[0]) + (b[3] - b[1]))
        total += 0.05 * math.hypot(x - ideal[0], y - ideal[1])
        total += 0.25 if ang % 180 == 90 else 0.05 if ang % 360 == 180 else 0.0
        if lado == "B":
            total += 0.8
        if rotulos_fijos:
            mis = lados_ocupa(ref, lado)
            total += sum(15.0 for zona_r, lado_r in rotulos_fijos if lado_r in mis and _cruzan(caja, zona_r))
        borde = min(caja[0] - x0, x1 - caja[2], caja[1] - y0, y1 - caja[3])

        def cercania(grupo, distancia, factor):
            return sum(factor * max(0.0, distancia - _gap(caja, cajas[o])) for o in grupos[grupo] if o != ref)

        if p.calor >= 2:
            total += 0.15 * borde + cercania("calientes", 8.0, 0.8) + cercania("sensibles", 10.0, 1.0)
        if p.sensible:
            total += cercania("calientes", 10.0, 1.0)
        if p.rapida and not es_conector(ref, p):
            total += cercania("calientes", 6.0, 0.5) + 0.5 * max(0.0, 3.0 - borde)
        if p.altura >= 8.0:
            total += cercania("finos", 4.0, 1.0)
        if p.fino:
            total += cercania("altos", 4.0, 1.0)
        return total

    def evaluar(ref, cx, cy, lados, ideal, region, mejor):
        for lado in lados:
            for ang in ANGULOS:
                if not valido(ref, cx, cy, ang, lado):
                    continue
                c = costo(ref, cx, cy, ang, lado, ideal)
                if region and not region[0] <= cx <= region[2]:
                    c += 5.0
                if mejor is None or c < mejor[0]:
                    mejor = (c, (cx, cy, ang, lado))
        return mejor

    def buscar(ref, ideal, radio=40.0, region=None, lados=None, barrido=True):
        lados = lados or lados_de[ref]
        preparar(ref)
        mejor = None
        for k in range(int(radio / PASO) + 1):
            for cx, cy in _anillo(ideal, k):
                if x0 < cx < x1 and y0 < cy < y1:
                    mejor = evaluar(ref, cx, cy, lados, ideal, region, mejor)
            if mejor and k > 6 and k % 4 == 0:
                break
        if mejor is None and barrido:
            yy = y0 + margen
            while yy < y1 - margen:
                xx = x0 + margen
                while xx < x1 - margen:
                    mejor = evaluar(ref, _rej(xx), _rej(yy), lados, ideal, None, mejor)
                    xx += 2.54
                yy += 2.54
        return mejor

    orden_log = []

    def colocar_en(ref, ideal, region=None, radio=40.0, lados=None) -> bool:
        res = buscar(ref, ideal, radio, region, lados)
        if res:
            poner(ref, res[1])
            orden_log.append(ref)
            return True
        return False

    def centroide(ref, solo_senal=True):
        pts = []
        for red in redes_de(ref):
            if solo_senal and es_potencia(red):
                continue
            pts += [q for o, lista in pads_red.get(red, {}).items() if o != ref for q in lista]
        return (sum(q[0] for q in pts) / len(pts), sum(q[1] for q in pts) / len(pts)) if pts else None

    modulos = [(t, [r for r in rs if r in pendientes]) for t, rs in placa._modulos()]
    modulos = [(t, rs) for t, rs in modulos if rs]
    sueltos = [r for r in pendientes if not any(r in rs for _, rs in modulos)]
    if sueltos:
        modulos.append(("resto", sueltos))

    def area(r):
        c = locales[r][lados_de[r][0]]["caja"]
        return (c[2] - c[0]) * (c[3] - c[1])

    areas = [max(40.0, sum(area(r) for r in rs)) for _, rs in modulos]
    total_area, regiones, xr = sum(areas) or 1.0, [], x0 + margen
    for a in areas:
        ancho = (x1 - x0 - 2 * margen) * a / total_area
        regiones.append((xr, y0 + margen, xr + ancho, y1 - margen))
        xr += ancho

    for (_titulo, rs), region in zip(modulos, regiones):
        centro = ((region[0] + region[2]) / 2, (region[1] + region[3]) / 2)
        conectores = [r for r in rs if es_conector(r, perfiles[r])]
        for r in conectores:
            ancho_c = locales[r]["F"]["caja"][2] - locales[r]["F"]["caja"][0] if "F" in locales[r] else 3
            borde_x = x0 + margen + ancho_c / 2 + 1 if centro[0] < (x0 + x1) / 2 else x1 - margen - ancho_c / 2 - 1
            colocar_en(r, (borde_x, centro[1]), region)
        resto = [r for r in rs if r not in conectores]
        if not resto:
            continue
        principal = max(resto, key=lambda r: (perfiles[r].calor < 2, len(locales[r][lados_de[r][0]]["pads"])))
        colocar_en(principal, centro, region)
        restantes = [r for r in resto if r != principal]
        desacoplo = [r for r in restantes if r.startswith("C") and len(redes_de(r)) == 2 and all(es_potencia(x) for x in redes_de(r))]
        for r in desacoplo:
            objetivo = None
            for red in redes_de(r):
                if not red.upper().lstrip("/").startswith("GND") and principal in pads_red.get(red, {}):
                    objetivo = pads_red[red][principal][0]
                    break
            colocar_en(r, objetivo or centro, region, radio=20.0)
        cristales = [r for r in restantes if r not in desacoplo and pf.prefijo(r) in ("Y", "X") and perfiles[r].sensible]
        for r in cristales:
            preparar(r)
            colocar_en(r, centroide(r) or centro, region, radio=15.0)
        calientes = [r for r in restantes if r not in desacoplo + cristales and perfiles[r].calor >= 2]
        for r in calientes:
            candidatos = [(region[0] + 2, centro[1]), (region[2] - 2, centro[1]), (centro[0], y0 + margen + 2), (centro[0], y1 - margen - 2)]
            ideal = min(candidatos, key=lambda q: min(q[0] - x0, x1 - q[0], q[1] - y0, y1 - q[1]) + 0.2 * math.dist(q, centro))
            colocar_en(r, ideal, region)
        otros = [r for r in restantes if r not in puestos]
        while otros:
            otros.sort(key=lambda r: sum(len(pads_red.get(red, {})) for red in redes_de(r) if not es_potencia(red)), reverse=True)
            r = otros.pop(0)
            colocar_en(r, centroide(r) or centro, region)

    for _ in range(2 if len(orden_log) <= 80 else 1):
        for r in list(orden_log):
            if es_conector(r, perfiles[r]):
                continue
            actual = retirar(r)
            preparar(r)
            ideal = centroide(r, solo_senal=False) or actual[:2]
            costo_actual = costo(r, *actual, ideal)
            res = buscar(r, ideal, radio=10.0, barrido=False)
            poner(r, res[1] if res and res[0] < costo_actual - 0.5 else actual)

    faltan = [r for r in pendientes if r not in puestos]
    movibles = [r for r in puestos if r in pendientes]
    encuadre = ""
    if movibles:
        bx0, by0 = min(cajas[r][0] for r in puestos), min(cajas[r][1] for r in puestos)
        bx1, by1 = max(cajas[r][2] for r in puestos), max(cajas[r][3] for r in puestos)
        if placa.estimado and len(movibles) == len(puestos) and not faltan and not list(placa.board.Zones()):
            borde = 2.5
            nx0, ny0 = math.floor((bx0 - borde) * 2) / 2, math.floor((by0 - borde) * 2) / 2
            nx1, ny1 = math.ceil((bx1 + borde) * 2) / 2, math.ceil((by1 + borde) * 2) / 2
            placa._contorno_rect(nx0, ny0, nx1, ny1)
            x0, y0, x1, y1 = placa.contorno_mm
            encuadre = f"la placa se ciñe al acomodo: {nx1 - nx0:g} × {ny1 - ny0:g} mm"
        else:
            dx = round(((x0 + x1) / 2 - (bx0 + bx1) / 2) / 0.635) * 0.635
            dy = round(((y0 + y1) / 2 - (by0 + by1) / 2) / 0.635) * 0.635
            fijos = [r for r in puestos if r not in movibles]
            dentro = all(cajas[r][0] + dx >= x0 + margen and cajas[r][2] + dx <= x1 - margen
                         and cajas[r][1] + dy >= y0 + margen and cajas[r][3] + dy <= y1 - margen for r in movibles)
            libres = all(not _cruzan((cajas[r][0] + dx, cajas[r][1] + dy, cajas[r][2] + dx, cajas[r][3] + dy), cajas[f])
                         for r in movibles for f in fijos)
            if (dx or dy) and dentro and libres:
                for r in movibles:
                    x, y, ang, lado = retirar(r)
                    poner(r, (round(x + dx, 4), round(y + dy, 4), ang, lado))
                encuadre = "conjunto centrado en la placa"

    grande = len(orden_log) > 40
    for i, r in enumerate(orden_log):
        x, y, ang, lado = puestos[r]
        try:
            placa.colocar(r, x, y, ang, avisar=False, lado=lado)
        except ValueError as exc:
            faltan.append(r)
            placa._decir(f"No pude poner {r}: {exc}", "acomodar", nivel="error")
            continue
        texto = f"Acomodo {r} ({placa.huellas[r].GetValue()}) en ({x:.2f}, {y:.2f}) a {ang:g}°{' en la cara inferior' if lado == 'B' else ''}"
        if grande:
            placa._decir(texto, "acomodar")
            if i % 10 == 9:
                placa.guardar()
        else:
            placa._paso(texto, "acomodar")

    rotulos_movidos = placa._acomodar_rotulos()
    rotulos_encima = placa.rotulos_encimados()
    movidas = placa._ordenar_referencias()
    longitud = placa.longitud_conexiones()
    placa.etapa = "acomodada"
    puestas = [r for r in orden_log if r in placa.colocadas]
    abajo = [r for r in puestas if puestos[r][3] == "B"]
    calientes = [r for r in puestas if perfiles[r].calor >= 2]
    sensibles_cerca = sorted({r for r in puestas if perfiles[r].sensible for o in calientes if _gap(cajas[r], cajas[o]) < 5})
    rapidas_piezas = [r for r in puestas if pf.prefijo(r) in ("Y", "X") and perfiles[r].sensible]
    partes = [f"Acomodo terminado: {len(puestas)} piezas", f"conexiones pendientes {longitud:.1f} mm"]
    if abajo:
        partes.append(f"{len(abajo)} en la cara inferior ({', '.join(abajo[:8])}{'…' if len(abajo) > 8 else ''})")
    if calientes:
        partes.append(f"calientes hacia el borde y con aire: {', '.join(calientes[:8])}{'…' if len(calientes) > 8 else ''}")
        partes.append("sensibles lejos del calor" if not sensibles_cerca else f"sensibles aún a menos de 5 mm de algo caliente: {', '.join(sensibles_cerca)}")
    if rapidas_piezas:
        partes.append(f"cristales junto a su integrado: {', '.join(rapidas_piezas)}")
    if puestas:
        alta = max(puestas, key=lambda r: perfiles[r].altura)
        partes.append(f"la más alta mide {perfiles[alta].altura:.1f} mm ({alta}, {perfiles[alta].fuente_altura})"
                      + (f"; tope pedido {altura_max:g} mm" if altura_max else ""))
    if excedidas:
        partes.append(f"superan el tope y no pueden ir abajo: {', '.join(excedidas)}")
    if encuadre:
        partes.append(encuadre)
    if rotulos_encima:
        partes.append(f"quedan {len(rotulos_encima)} rótulos de la placa encima de piezas, sin hueco cerca: "
                      f"{', '.join(rotulos_encima[:6])}{'…' if len(rotulos_encima) > 6 else ''}")
    if rotulos_movidos:
        partes.append(f"corrí {rotulos_movidos} rótulos que pisaban pads o piezas")
    if movidas:
        partes.append(f"reubiqué {movidas} referencias de serigrafía")
    if faltan:
        partes.append(f"no cupieron {', '.join(faltan)} (hace falta más placa o la otra cara)")
    placa._paso("; ".join(partes), "acomodar")
    return {"colocadas": len(puestas), "faltan": faltan, "longitud_mm": round(longitud, 1), "abajo": abajo,
            "calientes": calientes, "sensibles_cerca": sensibles_cerca, "excedidas": excedidas}
