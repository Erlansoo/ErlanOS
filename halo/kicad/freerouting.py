"""Freerouting como herramienta supervisada, nunca como jefe.

La IA decide qué redes se rutean y con qué reglas; este módulo:
  - localiza Freerouting y un Java que lo corra (Freerouting 2.4 necesita Java 25);
  - prepara el Specctra DSN que exporta KiCad: sólo las redes pedidas van a la red de
    Freerouting, las pistas que ya existen de otras redes quedan protegidas, y cada red lleva su
    ancho, holgura y vía (las clases de red del proyecto), no las que traiga el DSN por defecto;
  - lo corre sin interfaz y resume su resultado.
Aceptar o rechazar lo decide Placa.rutear_freerouting con el DRC de KiCad.
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

PAQUETES = Path.home() / "PCB" / ".tools" / "packages"


def _numeros(texto: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", texto)[:4]) or (0,)


def localizar() -> tuple[Path, Path]:
    """(java, jar). Se puede forzar con HALO_JAVA y HALO_FREEROUTING_JAR."""
    jar = os.environ.get("HALO_FREEROUTING_JAR")
    jars = [jar] if jar else sorted(
        glob.glob(str(PAQUETES / "freerouting-*.jar"))
        + glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\freerouting\app\freerouting*.jar"))
        + glob.glob(r"C:\Program Files\freerouting\app\freerouting*.jar"),
        key=lambda r: _numeros(Path(r).name))
    java = os.environ.get("HALO_JAVA")
    javas = [java] if java else sorted(glob.glob(str(PAQUETES / "jdk-*" / "bin" / "java.exe")),
                                       key=lambda r: _numeros(Path(r).parent.parent.name), reverse=True)
    if not java and os.environ.get("JAVA_HOME"):
        javas.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"))
    jars = [j for j in jars if j and Path(j).is_file()]
    javas = [j for j in javas if j and Path(j).is_file()]
    if not jars:
        raise RuntimeError("no encuentro freerouting-*.jar (define HALO_FREEROUTING_JAR)")
    if not javas:
        raise RuntimeError("Freerouting 2.4 necesita Java 25 y no encuentro uno (define HALO_JAVA)")
    return Path(javas[0]), Path(jars[-1])


def comando(java: Path, jar: Path, dsn: Path, ses: Path, pasadas: int, hilos: int = 1) -> list[str]:
    return [str(java), "-Djava.awt.headless=true", "-jar", str(jar), "-de", str(dsn), "-do", str(ses),
            "-mp", str(pasadas), "-mt", str(hilos)]


def entorno() -> dict:
    """Sin ventana y sin telemetría, sólo para este proceso."""
    env = dict(os.environ)
    env["FREEROUTING__GUI__ENABLED"] = "false"
    env["FREEROUTING__USAGE_AND_DIAGNOSTIC_DATA__DISABLE_ANALYTICS"] = "true"
    env["FREEROUTING__PROFILE__ALLOW_TELEMETRY"] = "false"
    return env


def resumen_salida(texto: str) -> dict:
    hallados = re.findall(r"final score: ([\d.,]+) \((\d+) unrouted and (\d+) violations\)", texto)
    if not hallados:
        return {}
    puntaje, sin_rutear, violaciones = hallados[-1]
    return {"puntaje": puntaje, "sin_rutear": int(sin_rutear), "violaciones": int(violaciones)}


# ------------------------------------------------------------ Specctra DSN
def _fin_bloque(t: str, i: int) -> int:
    nivel, comillas = 0, False
    for j in range(i, len(t)):
        c = t[j]
        if comillas:
            if c == '"':
                comillas = False
        elif c == '"':
            comillas = True
        elif c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
            if nivel == 0:
                return j + 1
    raise ValueError("DSN desbalanceado")


def _hijos(t: str, i: int, fin: int):
    j, comillas = i + 1, False
    while j < fin - 1:
        c = t[j]
        if comillas:
            if c == '"':
                comillas = False
        elif c == '"':
            comillas = True
        elif c == "(":
            k = _fin_bloque(t, j)
            yield j, k
            j = k
            continue
        j += 1


def _seccion(t: str, nombre: str):
    m = re.search(r"\n\s*\(" + nombre + r"\b", t)
    if not m:
        return None
    i = t.index("(", m.start())
    return i, _fin_bloque(t, i)


def _nombre(texto: str) -> str:
    return texto[1:-1] if texto.startswith('"') and texto.endswith('"') else texto


def _citar(nombre: str) -> str:
    return nombre if re.fullmatch(r'[^\s()"]+', nombre) else f'"{nombre}"'


def preparar_dsn(ruta: Path, objetivo: set | None, reglas: dict, holgura: float, planos: set | None = None) -> dict:
    """reglas: red -> (ancho, holgura, diámetro de vía, taladro), en mm. planos: capas que Freerouting no debe tocar."""
    t = Path(ruta).read_text(encoding="utf-8")

    # KiCad exporta todas las capas de cobre como (type signal); las de plano se declaran (type power)
    # para que Freerouting no tienda pistas en ellas y no parta el plano en islas.
    for capa in planos or ():
        t = re.sub(r"(\(layer " + re.escape(capa) + r"\s*\n\s*\(type )signal\)", r"\1power)", t)

    def um(v: float) -> str:
        return str(round(v * 1000))

    anchos = [r[0] for r in reglas.values()] or [0.25]
    si, sf = _seccion(t, "structure")
    ri = t.find("(rule", si, sf)
    if ri >= 0:
        rf = _fin_bloque(t, ri)
        regla = re.sub(r"\(width [\d.]+\)", f"(width {um(min(anchos))})", t[ri:rf], count=1)
        regla = re.sub(r"\(clearance [\d.]+\)", f"(clearance {um(holgura)})", regla, count=1)
        t = t[:ri] + regla + t[rf:]

    con_cable, protegidas = set(), 0
    seccion = _seccion(t, "wiring")
    if seccion:
        wi, wf = seccion
        partes, ultimo = [], wi
        for a, b in list(_hijos(t, wi, wf)):
            hijo = t[a:b]
            m = re.search(r'\(net\s+("[^"]*"|[^\s()]+)\)', hijo)
            red = _nombre(m.group(1)) if m else ""
            con_cable.add(red)
            if objetivo is not None and red not in objetivo:
                nuevo = re.sub(r"\(type\s+route\)", "(type protect)", hijo)
                protegidas += nuevo != hijo
                hijo = nuevo
            partes += [t[ultimo:a], hijo]
            ultimo = b
        partes.append(t[ultimo:wf])
        t = t[:wi] + "".join(partes) + t[wf:]

    ni, nf = _seccion(t, "network")
    bloques, enviadas = [], []
    for a, b in list(_hijos(t, ni, nf)):
        hijo = t[a:b]
        cabeza = re.match(r'\(\s*(\w+)\s+("[^"]*"|[^\s()]+)', hijo)
        if not cabeza or cabeza.group(1) != "net":
            continue
        red = _nombre(cabeza.group(2))
        if objetivo is None or red in objetivo or red in con_cable:
            bloques.append(hijo)
            enviadas.append(red)

    si, sf = _seccion(t, "structure")
    lista = re.search(r'\(via\s+((?:"[^"]*"\s*)+)\)', t[si:sf])
    nombres_via = re.findall(r'"([^"]*)"', lista.group(1)) if lista else []
    base = nombres_via[0] if nombres_via else None

    def via_para(diametro: float, taladro: float):
        nonlocal t
        medida = f"_{um(diametro)}:{um(taladro)}_um"
        existente = next((n for n in nombres_via if n.endswith(medida)), None)
        if existente or base is None:
            return existente or base
        pi = t.find(f'(padstack "{base}"')
        if pi < 0:
            return base
        pf = _fin_bloque(t, pi)
        nombre = re.sub(r"_\d+:\d+_um$", medida, base)
        clon = t[pi:pf].replace(f'"{base}"', f'"{nombre}"')
        clon = re.sub(r"\(circle (\S+) [\d.]+", lambda m: f"(circle {m.group(1)} {um(diametro)}", clon)
        t = t[:pf] + "\n    " + clon + t[pf:]
        t = t.replace('(via "', f'(via "{nombre}" "', 1)
        nombres_via.append(nombre)
        return nombre

    grupos: dict[tuple, list[str]] = {}
    for red in enviadas:
        ancho, h, diametro, taladro = reglas.get(red, (min(anchos), holgura, 0.6, 0.3))
        grupos.setdefault((ancho, h, via_para(diametro, taladro)), []).append(red)
    clases = []
    for n, ((ancho, h, via), miembros) in enumerate(sorted(grupos.items(), key=lambda kv: (kv[0][0], kv[0][1], str(kv[0][2])))):
        circuito = f'\n      (circuit\n        (use_via "{via}")\n      )' if via else ""
        clases.append(f"    (class halo_{n}_{um(ancho)}um {' '.join(_citar(r) for r in miembros)}{circuito}\n"
                      f"      (rule\n        (width {um(ancho)})\n        (clearance {um(h)})\n      )\n    )")
    ni, nf = _seccion(t, "network")
    red_nueva = "(network\n    " + "\n    ".join(bloques) + ("\n" if bloques else "") + "\n".join(clases) + "\n  )"
    t = t[:ni] + red_nueva + t[nf:]
    Path(ruta).write_text(t, encoding="utf-8")
    return {"enviadas": enviadas, "a_rutear": sorted(objetivo) if objetivo is not None else enviadas,
            "protegidas": int(protegidas), "clases": len(grupos), "vias": sorted({str(k[2]) for k in grupos})}
