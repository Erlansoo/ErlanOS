"""Dibuja el icono del Halo (aureola dorada sobre los tres puntos, en un cuadro café) sin ninguna biblioteca gráfica.

    python halo/lanzador/icono.py     -> build/halo/halo.ico (16 a 256 px) y halo/ui/halo.png (64 px)

Cada tamaño se dibuja aparte: en 24 px o menos los trazos engordan y se quitan los brillos para que se lea.
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TAMANOS = (16, 24, 32, 48, 64, 128, 256)

CAFE_ARRIBA = (78, 55, 40)
CAFE_ABAJO = (28, 19, 13)
LUZ_CENTRO = (128, 90, 60)
BORDE = (168, 126, 90)
ORO = (247, 200, 110)
ORO_BRILLO = (240, 168, 74)
CREMA = (248, 238, 224)
BRILLO_PUNTOS = (255, 226, 180)


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


def ico(imagenes: list[tuple[int, bytes]]) -> bytes:
    """ICO con una imagen PNG por tamaño (válido desde Windows Vista)."""
    cabecera = struct.pack("<HHH", 0, 1, len(imagenes))
    entradas, datos = b"", b""
    desplazamiento = 6 + 16 * len(imagenes)
    for lado, imagen in imagenes:
        entradas += struct.pack("<BBBBHHII", lado % 256, lado % 256, 0, 0, 1, 32, len(imagen), desplazamiento + len(datos))
        datos += imagen
    return cabecera + entradas + datos


def mezclar(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def encima(fondo, color, alfa):
    """Pinta color con cobertura alfa sobre fondo (r, g, b, a)."""
    a = alfa + fondo[3] * (1 - alfa)
    if a <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    k = alfa / a
    return (*(fondo[i] + (color[i] - fondo[i]) * k for i in range(3)), a)


def cuadro_redondeado(x, y, mitad, radio):
    """Distancia con signo a un cuadrado centrado de lado 2·mitad y esquinas de radio dado."""
    qx, qy = abs(x) - mitad + radio, abs(y) - mitad + radio
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radio


def aureola(lado: int):
    """Cuadro café con luz cálida, aureola dorada que flota y tres puntos crema debajo."""
    chico = lado <= 24
    muestras = 4 if lado <= 64 else 3                     # supermuestreo por pixel para bordes suaves
    c = lado / 2
    mitad, radio = lado * 0.46, lado * (0.26 if chico else 0.235)
    separacion = lado * (0.205 if chico else 0.165)
    r_punto = lado * (0.088 if chico else 0.062)
    y_puntos = lado * (0.11 if chico else 0.085)
    ax, ay = lado * (0.30 if chico else 0.25), lado * (0.09 if chico else 0.075)
    y_aureola = -lado * (0.19 if chico else 0.17)
    grosor = lado * (0.085 if chico else 0.034)

    def muestra(x, y):
        color = (0.0, 0.0, 0.0, 0.0)
        d_cuadro = cuadro_redondeado(x, y, mitad, radio)
        if d_cuadro > 0:
            return color
        t = (y + mitad) / (2 * mitad)
        color = encima(color, mezclar(CAFE_ARRIBA, CAFE_ABAJO, t ** 0.9), 1.0)
        color = encima(color, LUZ_CENTRO, 0.42 * math.exp(-(x * x + y * y) / (2 * (lado * 0.33) ** 2)))
        if d_cuadro > -lado * 0.024:                      # borde claro para que se vea en barras oscuras
            color = encima(color, BORDE, 0.6 * (1 + d_cuadro / (lado * 0.024)))

        v = y - y_aureola                                 # aureola: anillo elíptico, más claro adelante
        f = math.hypot(x, v * ax / ay)
        gradiente = math.hypot(x, v * (ax / ay) ** 2) / max(f, 1e-9)
        d = abs((f - ax) / gradiente) - grosor / 2
        if not chico:
            if f < ax:
                color = encima(color, ORO_BRILLO, 0.16)
            color = encima(color, ORO_BRILLO, 0.4 * math.exp(-max(d, 0) ** 2 / (2 * (lado * 0.038) ** 2)))
        if d <= 0:
            color = encima(color, mezclar(ORO_BRILLO, ORO, 1.0 if v > 0 else 0.7), 1.0)

        for px in (-separacion, 0.0, separacion):         # los tres puntos del Halo
            dp = math.hypot(x - px, y - y_puntos) - r_punto
            if not chico:
                color = encima(color, BRILLO_PUNTOS, 0.22 * math.exp(-max(dp, 0) ** 2 / (2 * (lado * 0.035) ** 2)))
            if dp <= 0:
                color = encima(color, CREMA, 1.0)
        return color

    def pixel(x, y):
        suma = [0.0, 0.0, 0.0, 0.0]
        for sy in range(muestras):
            for sx in range(muestras):
                r, g, b, a = muestra(x + (sx + 0.5) / muestras - c, y + (sy + 0.5) / muestras - c)
                suma[0] += r * a
                suma[1] += g * a
                suma[2] += b * a
                suma[3] += a
        if suma[3] <= 0:
            return (0, 0, 0, 0)
        return (*(int(round(suma[i] / suma[3])) for i in range(3)), int(round(255 * suma[3] / muestras ** 2)))

    return pixel


def main() -> int:
    destino_ico = REPO / "build" / "halo" / "halo.ico"
    destino_png = REPO / "halo" / "ui" / "halo.png"
    destino_ico.parent.mkdir(parents=True, exist_ok=True)
    imagenes = [(lado, png(lado, lado, aureola(lado))) for lado in TAMANOS]
    destino_ico.write_bytes(ico(imagenes))
    destino_png.write_bytes(dict(imagenes)[64])
    print(f"{destino_ico} ({len(imagenes)} tamaños: {', '.join(str(lado) for lado, _ in imagenes)} px)")
    print(destino_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
