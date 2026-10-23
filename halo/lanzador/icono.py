"""Dibuja el icono del Halo (el halo de tres puntos sobre el fondo café) sin ninguna biblioteca gráfica.

    python halo/lanzador/icono.py     -> build/halo/halo.ico (256 px) y halo/ui/halo.png (64 px)
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def png(ancho: int, alto: int, pixel) -> bytes:
    """PNG RGBA a partir de una función pixel(x, y) -> (r, g, b, a)."""
    filas = bytearray()
    for y in range(alto):
        filas.append(0)
        for x in range(ancho):
            filas.extend(pixel(x, y))

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(bytes(filas), 9)) + trozo(b"IEND", b""))


def ico(imagen_png: bytes, lado: int) -> bytes:
    """ICO con una sola imagen PNG (válido desde Windows Vista)."""
    cabecera = struct.pack("<HHH", 0, 1, 1)
    entrada = struct.pack("<BBBBHHII", lado % 256, lado % 256, 0, 0, 1, 32, len(imagen_png), 22)
    return cabecera + entrada + imagen_png


def halo(lado: int):
    """Disco café con luz en el centro y tres puntos claros, con borde suave."""
    c = lado / 2
    radio = lado * 0.47
    puntos = [(c - lado * 0.19, c), (c, c), (c + lado * 0.19, c)]
    r_punto = lado * 0.055

    def mezclar(a, b, t):
        return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

    def pixel(x, y):
        dx, dy = x + 0.5 - c, y + 0.5 - c
        d = math.hypot(dx, dy)
        alfa = max(0.0, min(1.0, radio - d + 0.5))           # borde del disco suavizado
        if alfa <= 0:
            return (0, 0, 0, 0)
        t = min(1.0, d / radio)
        color = mezclar((109, 76, 51), (42, 29, 20), t ** 0.85)  # del centro cálido al borde oscuro
        brillo = 0.0
        for px, py in puntos:
            dp = math.hypot(x + 0.5 - px, y + 0.5 - py)
            brillo = max(brillo, max(0.0, min(1.0, r_punto - dp + 0.5)))          # el punto
            brillo = max(brillo, 0.35 * max(0.0, 1 - (dp - r_punto) / (r_punto * 1.6)) if dp > r_punto else brillo)
        color = mezclar(color, (236, 223, 207), brillo)
        return (*color, int(round(255 * alfa)))

    return pixel


def main() -> int:
    destino_ico = REPO / "build" / "halo" / "halo.ico"
    destino_png = REPO / "halo" / "ui" / "halo.png"
    destino_ico.parent.mkdir(parents=True, exist_ok=True)
    grande = png(256, 256, halo(256))
    destino_ico.write_bytes(ico(grande, 256))
    destino_png.write_bytes(png(64, 64, halo(64)))
    print(f"{destino_ico} ({len(grande)} bytes de PNG)")
    print(destino_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
