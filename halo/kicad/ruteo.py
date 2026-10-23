"""Enrutado ortogonal de cables en la rejilla de 1,27 mm con A*.

Reglas que garantizan que el dibujo significa lo que se quiso conectar:
- nunca atraviesa el cuerpo ni la línea de un pin de un símbolo;
- nunca pasa por la punta de un pin que no es su destino;
- nunca corre sobre un cable de otra red ni toca su extremo (eso los uniría);
- puede cruzar en perpendicular un cable ajeno, pero no girar ni terminar sobre él;
- sale de cada pin en su dirección natural y llega a un pin por su lado exterior.
Costo: longitud + penalización por giro y por cruce, para trazos limpios.
"""
from __future__ import annotations

import heapq
import math

G = 1.27
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def celda(p) -> tuple[int, int]:
    return (round(p[0] / G), round(p[1] / G))


def punto(c) -> tuple[float, float]:
    return (round(c[0] * G, 4), round(c[1] * G, 4))


def celdas_segmento(a, b) -> list[tuple[int, int]]:
    if a[0] == b[0]:
        paso = 1 if b[1] >= a[1] else -1
        return [(a[0], j) for j in range(a[1], b[1] + paso, paso)]
    if a[1] == b[1]:
        paso = 1 if b[0] >= a[0] else -1
        return [(i, a[1]) for i in range(a[0], b[0] + paso, paso)]
    raise ValueError(f"segmento no ortogonal {a} -> {b}")


class Mapa:
    """Ocupación de la hoja: cuerpos, pines, cables por orientación y etiquetas."""

    def __init__(self, esquema):
        self.bloq: dict[tuple, str] = {}
        self.tips: dict[tuple, tuple] = {}
        self.h: dict[tuple, set] = {}
        self.v: dict[tuple, set] = {}
        self.ext: dict[tuple, set] = {}
        self.etiquetas: dict[tuple, str] = {}
        x0, y0, x1, y1 = esquema.area_util()
        self.limites = (math.ceil(x0 / G), math.ceil(y0 / G), math.floor(x1 / G), math.floor(y1 / G))
        for s in esquema.simbolos.values():
            bx0, by0, bx1, by1 = esquema.caja(s)
            for i in range(math.ceil(bx0 / G - 1e-6), math.floor(bx1 / G + 1e-6) + 1):
                for j in range(math.ceil(by0 / G - 1e-6), math.floor(by1 / G + 1e-6) + 1):
                    self.bloq[(i, j)] = s.ref
            for p in esquema.pines_hoja(s):
                tc, fc = celda(p["tip"]), celda(p["fin"])
                self.tips[tc] = (s.ref, p["numero"], esquema.pin_red.get((s.ref, p["numero"])))
                if tc != fc:
                    for c in celdas_segmento(tc, fc)[1:]:
                        self.bloq[c] = s.ref
        for tc in self.tips:
            self.bloq.pop(tc, None)
        for b in getattr(esquema, "bloques", []):
            (bx0, by0), (bx1, by1) = celda(b["a"]), celda(b["b"])
            for i in range(bx0, bx1 + 1):
                for c in ((i, by0), (i, by1)):
                    self.bloq.setdefault(c, f"el borde del bloque {b['titulo'] or 'sin título'}")
            for j in range(by0, by1 + 1):
                for c in ((bx0, j), (bx1, j)):
                    self.bloq.setdefault(c, f"el borde del bloque {b['titulo'] or 'sin título'}")
        for w in esquema.cables:
            self.agregar_cable(w["a"], w["b"], w["red"])
        for e in esquema.etiquetas:
            self.etiquetas[celda(e["at"])] = e["red"]

    def agregar_cable(self, a, b, red):
        ca, cb = celda(a), celda(b)
        tabla = self.h if ca[1] == cb[1] else self.v
        for c in celdas_segmento(ca, cb):
            tabla.setdefault(c, set()).add(red)
        for c in (ca, cb):
            self.ext.setdefault(c, set()).add(red)

    @staticmethod
    def _ajenas(tabla, c, red):
        s = tabla.get(c)
        return bool(s) and bool(s - {red})

    def ruta(self, inicio, salida, objetivos: dict, red: str, margen: int = 30):
        """Camino de celdas desde `inicio` (saliendo en `salida`) hasta un objetivo.

        objetivos: celda -> dirección de llegada exigida (o None).
        """
        if not objetivos:
            return None
        xs = [inicio[0]] + [c[0] for c in objetivos]
        ys = [inicio[1]] + [c[1] for c in objetivos]
        lx0, ly0, lx1, ly1 = self.limites
        wx0, wx1 = max(lx0, min(xs) - margen), min(lx1, max(xs) + margen)
        wy0, wy1 = max(ly0, min(ys) - margen), min(ly1, max(ys) + margen)
        gx0, gx1 = min(c[0] for c in objetivos), max(c[0] for c in objetivos)
        gy0, gy1 = min(c[1] for c in objetivos), max(c[1] for c in objetivos)

        def h(c):
            return max(gx0 - c[0], 0, c[0] - gx1) + max(gy0 - c[1], 0, c[1] - gy1)

        def libre(c, d, es_objetivo):
            if not (wx0 <= c[0] <= wx1 and wy0 <= c[1] <= wy1):
                return False
            if c in self.bloq:
                return False
            if c in self.tips and not es_objetivo:
                return False
            if self._ajenas(self.ext, c, red):
                return False
            if c in self.etiquetas and self.etiquetas[c] != red:
                return False
            if d[1] == 0 and self._ajenas(self.h, c, red):
                return False
            if d[0] == 0 and self._ajenas(self.v, c, red):
                return False
            return True

        def sobre_ajeno(c):
            return self._ajenas(self.h, c, red) or self._ajenas(self.v, c, red)

        def junto_a_cuerpo(c):
            return any((c[0] + dx, c[1] + dy) in self.bloq for dx, dy in DIRS)

        primero = (inicio[0] + salida[0], inicio[1] + salida[1])
        if not libre(primero, salida, primero in objetivos):
            return None
        clave0 = (primero, salida)
        padre = {clave0: None}
        mejor = {clave0: 1.0}
        abiertos = [(1.0 + h(primero), 1.0, primero, salida)]
        cerrados = set()
        while abiertos:
            _, g, c, d = heapq.heappop(abiertos)
            if (c, d) in cerrados:
                continue
            cerrados.add((c, d))
            if c in objetivos and (objetivos[c] is None or objetivos[c] == d) and not sobre_ajeno(c):
                camino = [c]
                k = (c, d)
                while padre[k] is not None:
                    k = padre[k]
                    camino.append(k[0])
                camino.append(inicio)
                camino.reverse()
                return camino
            if c in objetivos and objetivos[c] is not None:
                continue  # pin destino alcanzado por el lado equivocado: no se atraviesa
            for nd in DIRS:
                if nd == (-d[0], -d[1]):
                    continue
                gira = nd != d
                if gira and sobre_ajeno(c):
                    continue
                n = (c[0] + nd[0], c[1] + nd[1])
                if not libre(n, nd, n in objetivos):
                    continue
                costo = g + 1.0 + (3.0 if gira else 0.0)
                if sobre_ajeno(n):
                    costo += 4.0
                if junto_a_cuerpo(n):
                    costo += 0.4
                clave = (n, nd)
                if costo < mejor.get(clave, math.inf):
                    mejor[clave] = costo
                    padre[clave] = (c, d)
                    heapq.heappush(abiertos, (costo + h(n), costo, n, nd))
        return None


def esquinas(camino: list[tuple[int, int]]) -> list[tuple[float, float]]:
    """Reduce un camino de celdas a sus puntos de giro, en milímetros."""
    if len(camino) < 2:
        return [punto(c) for c in camino]
    puntos = [camino[0]]
    for i in range(1, len(camino) - 1):
        a, b, c = camino[i - 1], camino[i], camino[i + 1]
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            puntos.append(b)
    puntos.append(camino[-1])
    return [punto(c) for c in puntos]
