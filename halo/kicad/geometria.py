"""Geometría de símbolos de KiCad: la misma matriz de transformación que usa eeschema.

Biblioteca: Y hacia arriba. Hoja: Y hacia abajo. Un símbolo colocado en `(at x y rot)`
con `(mirror x|y)` lleva cada punto de biblioteca p a  at + T·p, donde T parte de
(1, 0, 0, -1) y se compone con rotaciones antihorarias y espejos como en SCH_SYMBOL.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Transform:
    x1: int
    y1: int
    x2: int
    y2: int

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.x1 * x + self.y1 * y, self.x2 * x + self.y2 * y)

    def compose(self, t: "Transform") -> "Transform":
        return Transform(self.x1 * t.x1 + self.x2 * t.y1,
                         self.y1 * t.x1 + self.y2 * t.y1,
                         self.x1 * t.x2 + self.x2 * t.y2,
                         self.y1 * t.x2 + self.y2 * t.y2)

    @property
    def swaps_axes(self) -> bool:
        return self.y1 != 0


BASE = Transform(1, 0, 0, -1)
CCW = Transform(0, 1, -1, 0)
CW = Transform(0, -1, 1, 0)
MIRROR_X = Transform(1, 0, 0, -1)
MIRROR_Y = Transform(-1, 0, 0, 1)


def transform(rotation: float, mirror: str | None = None) -> Transform:
    rot = int(round(rotation)) % 360
    t = BASE
    if rot == 90:
        t = t.compose(CCW)
    elif rot == 180:
        t = t.compose(CCW).compose(CCW)
    elif rot == 270:
        t = t.compose(CW)
    elif rot != 0:
        raise ValueError(f"KiCad sólo admite rotaciones de 90 grados, no {rotation}")
    if mirror == "x":
        t = t.compose(MIRROR_X)
    elif mirror == "y":
        t = t.compose(MIRROR_Y)
    return t


def place(at: tuple[float, float], t: Transform, p: tuple[float, float]) -> tuple[float, float]:
    dx, dy = t.apply(p[0], p[1])
    return (round(at[0] + dx, 4), round(at[1] + dy, 4))
