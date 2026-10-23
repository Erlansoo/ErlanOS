"""Markdown a HTML sin dependencias, para verlo dentro del Halo.

Reconoce lo que usa la documentación técnica: títulos, párrafos, listas anidadas, tablas, bloques de
código, citas, reglas, negrita, cursiva, código en línea, enlaces e imágenes. Todo el texto se escapa:
el HTML crudo nunca pasa al navegador (un documento que la IA escribe no puede inyectar nada).
"""
from __future__ import annotations

import html
import re
import unicodedata
from typing import Callable

_COMENTARIO = re.compile(r"<!--.*?-->", re.S)

_ENLACE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_CODIGO = re.compile(r"`([^`\n]+)`")
_NEGRITA = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_CURSIVA = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?!\w)")
_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LISTA = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLA_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_REGLA = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
ESQUEMAS = ("http://", "https://", "mailto:", "#", "/", "./", "../")


def _url_segura(url: str, resolver: Callable[[str], str] | None) -> str | None:
    bajo = url.strip().lower()
    if bajo.startswith(("javascript:", "data:", "vbscript:")):
        return None
    if bajo.startswith(("http://", "https://", "mailto:", "#")):
        return url
    return resolver(url) if resolver else url


def _ancla(texto: str) -> str:
    plano = html.unescape(re.sub(r"<[^>]+>", "", texto)).lower()
    sin_acentos = unicodedata.normalize("NFKD", plano).encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^\w\s-]", "", sin_acentos)
    return re.sub(r"[\s_]+", "-", limpio).strip("-") or "seccion"


def linea(texto: str, resolver: Callable[[str], str] | None = None) -> str:
    """Formato en línea: se escapa primero y se reconocen las marcas sobre el texto escapado."""
    trozos = []
    ultimo = 0
    for m in _CODIGO.finditer(texto):
        trozos.append(_marcas(texto[ultimo:m.start()], resolver))
        trozos.append("<code>" + html.escape(m.group(1)) + "</code>")
        ultimo = m.end()
    trozos.append(_marcas(texto[ultimo:], resolver))
    return "".join(trozos)


def _marcas(texto: str, resolver) -> str:
    salida = []
    ultimo = 0
    for m in _ENLACE.finditer(texto):
        salida.append(_enfasis(html.escape(texto[ultimo:m.start()])))
        etiqueta, url = m.group(1), m.group(2)
        destino = _url_segura(url, resolver)
        if destino is None:
            salida.append(html.escape(m.group(0)))
        elif m.group(0).startswith("!"):
            salida.append(f'<img src="{html.escape(destino, quote=True)}" alt="{html.escape(etiqueta, quote=True)}">')
        else:
            externo = ' target="_blank" rel="noopener"' if destino.startswith(("http://", "https://")) else ""
            salida.append(f'<a href="{html.escape(destino, quote=True)}"{externo}>{_enfasis(html.escape(etiqueta))}</a>')
        ultimo = m.end()
    salida.append(_enfasis(html.escape(texto[ultimo:])))
    return "".join(salida)


def _enfasis(escapado: str) -> str:
    escapado = _NEGRITA.sub(lambda m: "<strong>" + (m.group(1) or m.group(2)) + "</strong>", escapado)
    escapado = _CURSIVA.sub(lambda m: "<em>" + (m.group(1) or m.group(2)) + "</em>", escapado)
    return escapado.replace("  \n", "<br>\n")


def _tabla(filas: list[str], resolver) -> str:
    celdas = lambda f: [c.strip() for c in f.strip().strip("|").split("|")]  # noqa: E731
    cabecera = celdas(filas[0])
    alineacion = []
    for c in celdas(filas[1]):
        alineacion.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
    partes = ["<table><thead><tr>"]
    for i, c in enumerate(cabecera):
        partes.append(f'<th style="text-align:{alineacion[i] if i < len(alineacion) else "left"}">{linea(c, resolver)}</th>')
    partes.append("</tr></thead><tbody>")
    for f in filas[2:]:
        partes.append("<tr>")
        for i, c in enumerate(celdas(f)):
            partes.append(f'<td style="text-align:{alineacion[i] if i < len(alineacion) else "left"}">{linea(c, resolver)}</td>')
        partes.append("</tr>")
    partes.append("</tbody></table>")
    return "".join(partes)


def render(texto: str, resolver: Callable[[str], str] | None = None) -> str:
    # Los comentarios HTML (incluidas las marcas de sección del Halo) nunca llegan al lector.
    lineas = _COMENTARIO.sub("", texto.replace("\r\n", "\n")).split("\n")
    salida: list[str] = []
    parrafo: list[str] = []
    i = 0

    def cerrar_parrafo():
        if parrafo:
            salida.append("<p>" + linea("\n".join(parrafo), resolver) + "</p>")
            parrafo.clear()

    while i < len(lineas):
        l = lineas[i]
        if l.strip().startswith("```"):
            cerrar_parrafo()
            lenguaje = l.strip()[3:].strip()
            cuerpo = []
            i += 1
            while i < len(lineas) and not lineas[i].strip().startswith("```"):
                cuerpo.append(lineas[i])
                i += 1
            clase = f' class="lenguaje-{html.escape(lenguaje, quote=True)}"' if lenguaje.isalnum() else ""
            salida.append(f"<pre><code{clase}>" + html.escape("\n".join(cuerpo)) + "</code></pre>")
            i += 1
            continue
        m = _TITULO.match(l)
        if m:
            cerrar_parrafo()
            nivel = len(m.group(1))
            contenido = linea(m.group(2), resolver)
            salida.append(f'<h{nivel} id="{_ancla(contenido)}">{contenido}</h{nivel}>')
            i += 1
            continue
        if _REGLA.match(l):
            cerrar_parrafo()
            salida.append("<hr>")
            i += 1
            continue
        if l.lstrip().startswith(">"):
            cerrar_parrafo()
            cita = []
            while i < len(lineas) and lineas[i].lstrip().startswith(">"):
                cita.append(lineas[i].lstrip()[1:].lstrip())
                i += 1
            salida.append("<blockquote>" + render("\n".join(cita), resolver) + "</blockquote>")
            continue
        if "|" in l and i + 1 < len(lineas) and _TABLA_SEP.match(lineas[i + 1]):
            cerrar_parrafo()
            filas = []
            while i < len(lineas) and "|" in lineas[i] and lineas[i].strip():
                filas.append(lineas[i])
                i += 1
            salida.append(_tabla(filas, resolver))
            continue
        if _LISTA.match(l):
            cerrar_parrafo()
            i = _lista(lineas, i, salida, resolver)
            continue
        if not l.strip():
            cerrar_parrafo()
            i += 1
            continue
        parrafo.append(l.rstrip() if not l.endswith("  ") else l)
        i += 1
    cerrar_parrafo()
    return "\n".join(salida)


def _lista(lineas: list[str], i: int, salida: list[str], resolver) -> int:
    """Listas anidadas por sangría; devuelve la primera línea que ya no pertenece a la lista."""
    elementos: list[tuple[int, bool, list[str]]] = []   # (sangría, ordenada, líneas del elemento)
    while i < len(lineas):
        m = _LISTA.match(lineas[i])
        if m:
            elementos.append((len(m.group(1).expandtabs(4)), m.group(2)[0].isdigit(), [m.group(3)]))
            i += 1
        elif elementos and lineas[i].strip() and (lineas[i].startswith("  ") or lineas[i].startswith("\t")):
            elementos[-1][2].append(lineas[i].strip())
            i += 1
        else:
            break
    salida.append(_anidar(elementos, 0, len(elementos), resolver))
    return i


def _anidar(elementos, inicio, fin, resolver) -> str:
    if inicio >= fin:
        return ""
    base = elementos[inicio][0]
    partes: list[str] = []
    abierta = None   # una lista ordenada y una de viñetas seguidas son dos listas distintas
    j = inicio
    while j < fin:
        sangria, ordenada, texto = elementos[j]
        etiqueta = "ol" if ordenada else "ul"
        if abierta != etiqueta:
            if abierta:
                partes.append(f"</{abierta}>")
            partes.append(f"<{etiqueta}>")
            abierta = etiqueta
        k = j + 1
        while k < fin and elementos[k][0] > base:
            k += 1
        partes.append("<li>" + linea(" ".join(texto), resolver) + _anidar(elementos, j + 1, k, resolver) + "</li>")
        j = k
    if abierta:
        partes.append(f"</{abierta}>")
    return "".join(partes)
