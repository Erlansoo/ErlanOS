"""Documentos de un proyecto: viven en <carpeta>/docs, se ven en el Halo y los mantiene la IA.

- Markdown se dibuja aquí mismo (markdown.py); LaTeX se compila a PDF con MiKTeX y se ve en el Halo.
- Las secciones generadas llevan la revisión de su fuente (esquemático, simulación, placa, fabricación);
  si la fuente cambia, la sección aparece como desactualizada hasta que se regenera.
- La guía de etapas propone el siguiente paso; el proyecto puede saltárselo.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..kicad.sesion import escribir_atomico
from . import datos as _datos, markdown, plantillas

DOCS = "docs"
TIPOS = {".md": "markdown", ".tex": "latex", ".pdf": "pdf", ".docx": "docx", ".html": "html", ".txt": "texto", ".svg": "imagen", ".png": "imagen"}
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOME = Path.home()
_MARCA_MD = re.compile(r"^<!-- halo:seccion (\w+) (\S+) -->\s*$")
_FIN_MD = re.compile(r"^<!-- halo:fin (\w+) -->\s*$")
_MARCA_TEX = re.compile(r"^% halo:seccion (\w+) (\S+)\s*$")
_FIN_TEX = re.compile(r"^% halo:fin (\w+)\s*$")
ETAPAS = (("documentacion", "Documentación"), ("esquematico", "Esquemático"), ("simulacion", "Simulación"),
          ("pcb", "Placa"), ("fabricacion", "Fabricación"), ("3d", "3D y carcasa"))


class ErrorDocumento(ValueError):
    pass


def _herramienta(nombre: str, *candidatos: Path) -> str | None:
    forzado = os.environ.get("HALO_" + nombre.upper().replace("-", "_"))
    if forzado:
        return forzado if Path(forzado).is_file() else None
    encontrado = shutil.which(nombre)
    if encontrado:
        return encontrado
    return next((str(c) for c in candidatos if c.is_file()), None)


def kicad_cli() -> str | None:
    return _herramienta("kicad-cli", Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/KiCad/10.0/bin/kicad-cli.exe")


def pdflatex() -> str | None:
    return _herramienta("pdflatex", HOME / "AppData/Local/Programs/MiKTeX/miktex/bin/x64/pdflatex.exe")


def pandoc() -> str | None:
    return _herramienta("pandoc", HOME / "AppData/Local/Pandoc/pandoc.exe")


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]


def carpeta_docs(carpeta: Path) -> Path:
    return Path(carpeta).resolve() / DOCS


def _ruta(carpeta: Path, nombre: str) -> Path:
    if not nombre or "/" in nombre or "\\" in nombre or nombre.startswith(".") or nombre in ("..",):
        raise ErrorDocumento("Nombre de documento no válido")
    return carpeta_docs(carpeta) / nombre


def archivo(carpeta: Path, ruta: str) -> Path:
    """Un archivo dentro de docs/ (figuras, PDF); nunca fuera de esa carpeta."""
    base = carpeta_docs(carpeta)
    destino = (base / ruta).resolve()
    if base != destino and base not in destino.parents:
        raise ErrorDocumento("Ruta fuera de la carpeta de documentos")
    if not destino.is_file():
        raise ErrorDocumento("Ese archivo no existe")
    return destino


# ------------------------------------------------------------------ secciones
def _marcas(tipo: str):
    return (_MARCA_MD, _FIN_MD, plantillas.marca_md, plantillas.seccion_md) if tipo == "markdown" else (_MARCA_TEX, _FIN_TEX, plantillas.marca_tex, plantillas.seccion_tex)


def secciones(texto: str, tipo: str, revisiones: dict) -> list[dict]:
    inicio_re, fin_re, _, _ = _marcas(tipo)
    salida = []
    abierta = None
    for n, linea in enumerate(texto.splitlines(), 1):
        m = inicio_re.match(linea)
        if m:
            abierta = {"fuente": m.group(1), "revision": m.group(2), "linea": n}
            continue
        m = fin_re.match(linea)
        if m and abierta and abierta["fuente"] == m.group(1):
            actual = revisiones.get(abierta["fuente"], "ausente")
            abierta["estado"] = "vigente" if actual == abierta["revision"] else "desactualizada"
            abierta["titulo"] = plantillas.FUENTES.get(abierta["fuente"], abierta["fuente"])
            salida.append(abierta)
            abierta = None
    return salida


def regenerar(texto: str, tipo: str, d: dict) -> str:
    """Reemplaza el contenido de cada sección marcada por el generado ahora; lo de fuera no se toca."""
    inicio_re, fin_re, marca, seccion = _marcas(tipo)
    lineas = texto.splitlines()
    salida = []
    i = 0
    while i < len(lineas):
        m = inicio_re.match(lineas[i])
        if m and m.group(1) in plantillas.FUENTES:
            fuente = m.group(1)
            j = i + 1
            while j < len(lineas) and not (fin_re.match(lineas[j]) and fin_re.match(lineas[j]).group(1) == fuente):
                j += 1
            if j < len(lineas):
                ini, fin = marca(fuente, d["revisiones"].get(fuente, "ausente"))
                salida += [ini, seccion(fuente, d), fin]
                i = j + 1
                continue
        salida.append(lineas[i])
        i += 1
    return "\n".join(salida) + ("\n" if texto.endswith("\n") else "")


# ------------------------------------------------------------------ documentos
def listar(carpeta: Path) -> dict:
    base = carpeta_docs(carpeta)
    revisiones = _datos.revisiones(carpeta)
    documentos = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            if not p.is_file() or p.suffix.lower() not in TIPOS or p.name.startswith("."):
                continue
            tipo = TIPOS[p.suffix.lower()]
            entrada = {"nombre": p.name, "tipo": tipo, "tamano": p.stat().st_size,
                       "modificado": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}
            if tipo in ("markdown", "latex"):
                try:
                    secs = secciones(p.read_text(encoding="utf-8", errors="replace"), tipo, revisiones)
                except OSError:
                    secs = []
                entrada["secciones"] = secs
                entrada["desactualizadas"] = sum(1 for s in secs if s["estado"] == "desactualizada")
            if tipo == "latex":
                pdf = p.with_suffix(".pdf")
                entrada["pdf"] = pdf.name if pdf.is_file() else None
                entrada["pdf_vigente"] = bool(pdf.is_file() and pdf.stat().st_mtime >= p.stat().st_mtime)
            documentos.append(entrada)
    return {"documentos": documentos, "revisiones": revisiones, "guia": guia(carpeta, documentos, revisiones),
            "herramientas": {"pdflatex": bool(pdflatex()), "pandoc": bool(pandoc()), "kicad_cli": bool(kicad_cli())}}


def html_con_secciones(texto: str, revisiones: dict, resolver) -> str:
    """Markdown a HTML envolviendo cada sección generada para que el lector vea si sigue al día."""
    partes: list[str] = []
    libre: list[str] = []
    seccion: list[str] | None = None
    fuente = rev = ""

    def volcar_libre():
        if libre:
            partes.append(markdown.render("\n".join(libre), resolver))
            libre.clear()

    for linea in texto.replace("\r\n", "\n").split("\n"):
        m = _MARCA_MD.match(linea)
        if m and seccion is None:
            volcar_libre()
            fuente, rev = m.group(1), m.group(2)
            seccion = []
            continue
        m = _FIN_MD.match(linea)
        if m and seccion is not None and m.group(1) == fuente:
            estado = "vigente" if revisiones.get(fuente, "ausente") == rev else "desactualizada"
            aviso = ("" if estado == "vigente" else
                     f'<span class="doc-aviso">desactualizada: cambió {plantillas.FUENTES.get(fuente, fuente).lower()}; pulsa «Actualizar secciones»</span>')
            partes.append(f'<section class="doc-seccion {estado}" data-fuente="{fuente}"><span class="doc-marca">generada · {plantillas.FUENTES.get(fuente, fuente)}</span>'
                          f"{aviso}{markdown.render(chr(10).join(seccion), resolver)}</section>")
            seccion = None
            continue
        (libre if seccion is None else seccion).append(linea)
    if seccion is not None:
        libre.extend(seccion)
    volcar_libre()
    return "\n".join(partes)


def leer(carpeta: Path, nombre: str, figuras_url=None) -> dict:
    """Un documento para verlo: texto, HTML (Markdown) o PDF asociado (LaTeX). `figuras_url` convierte una ruta
    relativa (figuras/x.svg) en la dirección que la sirve; por defecto se marca con «@figura/»."""
    ruta = _ruta(carpeta, nombre)
    if not ruta.is_file():
        raise ErrorDocumento("Ese documento no existe")
    tipo = TIPOS.get(ruta.suffix.lower(), "otro")
    salida = {"nombre": ruta.name, "tipo": tipo}
    resolver = figuras_url or (lambda r: "@figura/" + r)
    if tipo in ("markdown", "latex", "texto", "html"):
        texto = ruta.read_text(encoding="utf-8", errors="replace")
        salida["texto"] = texto
        salida["firma"] = _sha(texto)
        revisiones = _datos.revisiones(carpeta)
        if tipo in ("markdown", "latex"):
            salida["secciones"] = secciones(texto, tipo, revisiones)
        if tipo == "markdown":
            salida["html"] = html_con_secciones(texto, revisiones, resolver)
        if tipo == "latex":
            pdf = ruta.with_suffix(".pdf")
            salida["pdf"] = pdf.name if pdf.is_file() else None
            salida["pdf_vigente"] = bool(pdf.is_file() and pdf.stat().st_mtime >= ruta.stat().st_mtime)
    return salida


def guardar(carpeta: Path, nombre: str, texto: str, firma: str | None = None) -> dict:
    ruta = _ruta(carpeta, nombre)
    if ruta.suffix.lower() not in (".md", ".tex", ".txt"):
        raise ErrorDocumento("Sólo se editan aquí documentos Markdown, LaTeX o de texto")
    if len(texto) > 4_000_000:
        raise ErrorDocumento("Documento demasiado grande")
    if ruta.is_file() and firma is not None and _sha(ruta.read_text(encoding="utf-8", errors="replace")) != firma:
        raise ErrorDocumento("El documento cambió en disco desde que lo abriste; vuelve a cargarlo")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    escribir_atomico(ruta, texto)
    return {"nombre": ruta.name, "firma": _sha(texto)}


def crear(carpeta: Path, plantilla: str, nombre: str | None = None, reemplazar: bool = False) -> dict:
    if plantilla not in ("memoria", "informe"):
        raise ErrorDocumento("Plantillas disponibles: memoria (Markdown) e informe (LaTeX)")
    d = _datos.recopilar(carpeta)
    nombre = nombre or ("memoria.md" if plantilla == "memoria" else "informe.tex")
    ruta = _ruta(carpeta, nombre)
    if ruta.is_file() and not reemplazar:
        raise ErrorDocumento(f"{nombre} ya existe; usa «actualizar» para regenerar sus secciones")
    texto = plantillas.memoria_md(d) if plantilla == "memoria" else plantillas.informe_tex(d)
    figuras(carpeta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    escribir_atomico(ruta, texto)
    return {"nombre": ruta.name, "firma": _sha(texto)}


def actualizar(carpeta: Path, nombre: str) -> dict:
    ruta = _ruta(carpeta, nombre)
    if not ruta.is_file():
        raise ErrorDocumento("Ese documento no existe")
    tipo = TIPOS.get(ruta.suffix.lower())
    if tipo not in ("markdown", "latex"):
        raise ErrorDocumento("Sólo se regeneran secciones en Markdown o LaTeX")
    d = _datos.recopilar(carpeta)
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    nuevo = regenerar(texto, tipo, d)
    figuras(carpeta)
    if nuevo != texto:
        escribir_atomico(ruta, nuevo)
    return {"nombre": ruta.name, "firma": _sha(nuevo), "secciones": secciones(nuevo, tipo, d["revisiones"]), "cambio": nuevo != texto}


# ------------------------------------------------------------------ figuras, LaTeX, pandoc
def _correr(comando: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(comando, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=SIN_VENTANA)


def figuras(carpeta: Path) -> dict:
    """Exporta con kicad-cli lo que citan los documentos (SVG para el Halo, PDF para LaTeX) y copia los renders 3D.
    Sólo rehace lo que esté más viejo que su fuente."""
    from .. import proyectos
    carpeta = Path(carpeta).resolve()
    destino = carpeta_docs(carpeta) / "figuras"
    archivos = proyectos.archivos_de(carpeta)
    cli = kicad_cli()
    hechas, errores = [], []

    def vieja(salida: Path, fuente: Path) -> bool:
        return not salida.is_file() or salida.stat().st_mtime < fuente.stat().st_mtime

    trabajos = []
    if archivos.get("esquematico"):
        sch = Path(archivos["esquematico"])
        trabajos.append((destino / "esquematico.svg", sch, ["sch", "export", "svg", "-o", str(destino), "-e", "-n", str(sch)], sch.stem + ".svg"))
        trabajos.append((destino / "esquematico.pdf", sch, ["sch", "export", "pdf", "-o", str(destino / "esquematico.pdf"), "-e", "-n", "--black-and-white", str(sch)], None))
    if archivos.get("pcb"):
        pcb = Path(archivos["pcb"])
        capas = "F.Cu,B.Cu,Edge.Cuts,F.SilkS"
        trabajos.append((destino / "placa.svg", pcb, ["pcb", "export", "svg", "--mode-single", "-o", str(destino / "placa.svg"), "-l", capas,
                                                    "--exclude-drawing-sheet", "--page-size-mode", "2", str(pcb)], None))
        trabajos.append((destino / "placa.pdf", pcb, ["pcb", "export", "pdf", "--mode-single", "-o", str(destino / "placa.pdf"), "-l", capas,
                                                    "--black-and-white", str(pcb)], None))
        for render in sorted((carpeta / ".halo").glob("3d-*.png")):
            copia = destino / render.name
            if vieja(copia, render):
                destino.mkdir(parents=True, exist_ok=True)
                shutil.copy2(render, copia)
                hechas.append(copia.name)
    for salida, fuente, args, renombrar in trabajos:
        if not vieja(salida, fuente):
            continue
        if not cli:
            errores.append("kicad-cli no está disponible")
            break
        destino.mkdir(parents=True, exist_ok=True)
        try:
            r = _correr([cli, *args], destino, 120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errores.append(f"{salida.name}: {exc}")
            continue
        if renombrar and (destino / renombrar).is_file() and renombrar != salida.name:
            os.replace(destino / renombrar, salida)
        if r.returncode != 0 or not salida.is_file():
            errores.append(f"{salida.name}: {(r.stderr or r.stdout).strip()[-300:]}")
        else:
            hechas.append(salida.name)
    return {"hechas": hechas, "errores": errores}


def compilar(carpeta: Path, nombre: str) -> dict:
    ruta = _ruta(carpeta, nombre)
    if ruta.suffix.lower() != ".tex" or not ruta.is_file():
        raise ErrorDocumento("Indica un documento .tex existente")
    exe = pdflatex()
    if not exe:
        raise ErrorDocumento("No encuentro pdflatex (MiKTeX); instálalo o define HALO_PDFLATEX")
    figuras(carpeta)
    base = ruta.parent
    salida = base / ".build"
    salida.mkdir(exist_ok=True)
    inicio = time.time()
    registro = ""
    for _ in range(2):   # dos pasadas: referencias, índices y longtable
        try:
            r = _correr([exe, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={salida}", ruta.name], base, 240)
        except subprocess.TimeoutExpired:
            raise ErrorDocumento("pdflatex tardó más de 4 minutos; revisa el documento") from None
        registro = r.stdout
        if r.returncode != 0:
            errores = [l for l in registro.splitlines() if l.startswith("!") or l.startswith("l.")]
            raise ErrorDocumento("LaTeX no compiló: " + (" | ".join(errores[:4]) or registro[-400:]))
    pdf = salida / ruta.with_suffix(".pdf").name
    if not pdf.is_file():
        raise ErrorDocumento("pdflatex terminó sin producir el PDF")
    shutil.copy2(pdf, base / pdf.name)
    return {"pdf": pdf.name, "segundos": round(time.time() - inicio, 1),
            "avisos": sum(1 for l in registro.splitlines() if l.startswith("LaTeX Warning"))}


def convertir(carpeta: Path, nombre: str, formato: str) -> dict:
    if formato not in ("docx", "pdf", "html", "tex", "md", "odt"):
        raise ErrorDocumento("Formatos: docx, pdf, html, tex, md, odt")
    ruta = _ruta(carpeta, nombre)
    if not ruta.is_file():
        raise ErrorDocumento("Ese documento no existe")
    exe = pandoc()
    if not exe:
        raise ErrorDocumento("No encuentro pandoc; instálalo o define HALO_PANDOC")
    figuras(carpeta)
    destino = ruta.with_suffix("." + formato)
    if destino == ruta:
        raise ErrorDocumento("El documento ya está en ese formato")
    comando = [exe, ruta.name, "-o", destino.name, "--resource-path=.", "-V", "lang=es", "--standalone"]
    if formato == "pdf":
        comando += ["--pdf-engine=pdflatex", "-V", "geometry:margin=2.5cm"]
    try:
        r = _correr(comando, ruta.parent, 240)
    except subprocess.TimeoutExpired:
        raise ErrorDocumento("pandoc tardó demasiado") from None
    if r.returncode != 0 or not destino.is_file():
        raise ErrorDocumento("pandoc no pudo convertir: " + (r.stderr or r.stdout).strip()[-400:])
    return {"archivo": destino.name}


# ------------------------------------------------------------------ guía de etapas
def guia(carpeta: Path, documentos: list[dict] | None = None, revisiones: dict | None = None) -> dict:
    """Estado de cada etapa y el paso que conviene dar; es una propuesta, no una regla."""
    from .. import proyectos
    carpeta = Path(carpeta).resolve()
    if documentos is None:
        documentos = listar(carpeta)["documentos"]
    halo = carpeta / ".halo"
    archivos = proyectos.archivos_de(carpeta)
    estados = []

    memorias = [d for d in documentos if d["tipo"] in ("markdown", "latex")]
    desact = sum(d.get("desactualizadas", 0) for d in memorias)
    if not memorias:
        estados.append(("documentacion", "pendiente", "sin memoria ni informe", "Crea la memoria (Markdown) para fijar propósito y alcance"))
    elif desact:
        estados.append(("documentacion", "en curso", f"{desact} sección(es) desactualizada(s)", "Actualiza las secciones que cambiaron"))
    else:
        estados.append(("documentacion", "lista", f"{len(memorias)} documento(s) al día", ""))

    ver = _datos._json(halo / "verificacion.json") or {}
    if not archivos.get("esquematico"):
        estados.append(("esquematico", "pendiente", "sin esquemático", "Dibuja el esquemático por bloques con halo.kicad.esquematico"))
    elif not ver.get("erc"):
        estados.append(("esquematico", "en curso", "dibujado, sin verificar", "Verifica con KiCad (ERC y netlist) antes de simular"))
    elif (ver.get("erc") or {}).get("errores") or not (ver.get("netlist") or {}).get("coinciden", True):
        estados.append(("esquematico", "en curso", plantillas._estado_erc(ver), "Corrige los errores de ERC o las diferencias de netlist"))
    else:
        estados.append(("esquematico", "lista", plantillas._estado_erc(ver), ""))

    sims = _datos._simulaciones(carpeta)
    if not sims:
        estados.append(("simulacion", "pendiente", "sin simulaciones", "Configura el banco (modelos, fuentes, medidas) y simula con ngspice"))
    elif any(s["estado"] == "completado" and s.get("vigencia") == "vigente" and s.get("criterios") in ("cumple", "sin_criterios") for s in sims):
        estados.append(("simulacion", "lista", f"{len(sims)} simulación(es), la última vigente", ""))
    else:
        estados.append(("simulacion", "en curso", f"{len(sims)} simulación(es) sin resultado vigente que cumpla", "Repite la simulación sobre la revisión actual o revisa los criterios"))

    placa = _datos._json(halo / "placa.json") or {}
    drc = _datos._json(halo / "verificacion-pcb.json") or {}
    if not archivos.get("pcb"):
        estados.append(("pcb", "pendiente", "sin placa", "Crea la placa (python -m halo.kicad.pcb <carpeta> crear) y acomoda"))
    elif placa.get("etapa") == "verificada" or (drc and not (drc.get("drc") or {}).get("errores") and not drc.get("pendientes")):
        estados.append(("pcb", "lista", plantillas._estado_drc({"drc": {"errores": (drc.get("drc") or {}).get("errores"), "avisos": (drc.get("drc") or {}).get("avisos"), "pendientes": drc.get("pendientes"), "paridad": drc.get("paridad")}}), ""))
    else:
        siguiente = {"creada": "acomodar", "acomodada": "rutear", "ruteada": "verificar"}.get(placa.get("etapa"), "verificar")
        estados.append(("pcb", "en curso", f"etapa {placa.get('etapa') or 'sin etapa'}", f"Sigue con «{siguiente}» en la placa"))

    fabs = _datos._fabricaciones(carpeta)
    if not fabs:
        estados.append(("fabricacion", "pendiente", "sin archivos de fabricación", "Prepara Gerbers y taladros para revisión"))
    elif any(f.get("veredicto") == "READY" for f in fabs):
        estados.append(("fabricacion", "lista", "entrega lista para revisión", ""))
    else:
        estados.append(("fabricacion", "en curso", f"última entrega: {fabs[0].get('veredicto')}", "Resuelve lo que bloquea la entrega y vuelve a preparar"))

    renders = sorted(halo.glob("3d-*.png"))
    if renders:
        estados.append(("3d", "lista", f"{len(renders)} render(s) de la placa", ""))
    else:
        estados.append(("3d", "pendiente", "sin renders", "Genera el 3D de la placa y, si hace falta, la carcasa en el espacio 3D"))

    nombres = dict(ETAPAS)
    etapas = [{"etapa": e, "nombre": nombres[e], "estado": est, "detalle": det, "sugerencia": sug} for e, est, det, sug in estados]
    pendiente = next((x for x in etapas if x["estado"] != "lista"), None)
    siguiente = f"{pendiente['nombre']}: {pendiente['sugerencia']}" if pendiente else "Todas las etapas están al día; mantén la documentación con cada cambio"
    return {"etapas": etapas, "siguiente": siguiente, "nota": "Las etapas son una guía, no una regla: el proyecto puede saltárselas."}


def resumen_guia(carpeta: Path) -> str:
    """Una línea por etapa, para el contexto de la IA."""
    try:
        g = guia(carpeta)
    except Exception as exc:  # noqa: BLE001
        return f"(guía no disponible: {exc})"
    partes = [f"{e['nombre']}: {e['estado']} ({e['detalle']})" for e in g["etapas"]]
    return "; ".join(partes) + f". Siguiente paso sugerido → {g['siguiente']}"


# ------------------------------------------------------------------ entrada HTTP
def operar(peticion: dict) -> dict:
    carpeta = Path(str(peticion.get("proyecto_id") or ""))
    if not carpeta.is_dir():
        raise ErrorDocumento("La petición no indica un proyecto abierto")
    op = peticion.get("op")
    nombre = str(peticion.get("nombre") or "")
    if op == "crear":
        return {"ok": True, **crear(carpeta, str(peticion.get("plantilla") or ""), str(peticion.get("nombre") or "") or None, bool(peticion.get("reemplazar")))}
    if op == "actualizar":
        return {"ok": True, **actualizar(carpeta, nombre)}
    if op == "guardar":
        return {"ok": True, **guardar(carpeta, nombre, str(peticion.get("texto") or ""), peticion.get("firma"))}
    if op == "compilar":
        return {"ok": True, **compilar(carpeta, nombre)}
    if op == "convertir":
        return {"ok": True, **convertir(carpeta, nombre, str(peticion.get("formato") or ""))}
    if op == "figuras":
        return {"ok": True, **figuras(carpeta)}
    raise ErrorDocumento("Operación de documentos desconocida")
