"""Proyectos abiertos en el Halo: cada uno es una pestaña con su carpeta, su estado y su bitácora.

El estado vivo de un proyecto está en <carpeta>/.halo/actual.json: lo escriben los motores (Sesion)
y el editor, y lo lee el servidor. Las pestañas abiertas, su orden y el activo se guardan en
build/halo/pestanas.json. Un motor que empieza a trabajar en una carpeta deja un aviso en
build/halo/abrir/<id>.json para que el Halo abra su pestaña si no estaba.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ESTADO = REPO / "build" / "halo"
_TITULO = re.compile(r'\(title\s+"((?:[^"\\]|\\.)*)"')


def identificador(carpeta) -> str:
    """El mismo proyecto_id que ya usan la edición, la simulación y la fabricación: la carpeta resuelta."""
    return str(Path(carpeta).resolve())


def clave(carpeta) -> str:
    return hashlib.sha1(identificador(carpeta).lower().encode("utf-8")).hexdigest()[:12]


def archivos_de(carpeta) -> dict:
    """Esquemático y placa de la carpeta: los del único .kicad_pro, o los únicos que haya."""
    carpeta = Path(carpeta)
    pros = sorted(carpeta.glob("*.kicad_pro"))
    salida = {}
    for etapa, extension in (("esquematico", ".kicad_sch"), ("pcb", ".kicad_pcb")):
        candidatos = [p for p in ([pros[0].with_suffix(extension)] if len(pros) == 1 else sorted(carpeta.glob("*" + extension)))
                      if p.is_file()]
        if len(candidatos) == 1 or (candidatos and len(pros) == 1):
            salida[etapa] = str(candidatos[0])
    return salida


def nombre_de(carpeta, archivos: dict | None = None) -> str:
    """Título del esquemático si lo tiene; si no, el nombre del proyecto o de la carpeta."""
    carpeta = Path(carpeta)
    archivos = archivos if archivos is not None else archivos_de(carpeta)
    sch = archivos.get("esquematico")
    if sch:
        try:
            with open(sch, encoding="utf-8", errors="replace") as fh:
                m = _TITULO.search(fh.read(16384))
            if m and m.group(1).strip():
                return m.group(1).replace('\\"', '"').strip()
        except OSError:
            pass
    pros = sorted(carpeta.glob("*.kicad_pro"))
    return pros[0].stem if len(pros) == 1 else carpeta.name


def estado_inicial(carpeta) -> dict:
    """Estado de un proyecto recién abierto, sin ningún motor trabajando en él."""
    carpeta = Path(carpeta).resolve()
    archivos = archivos_de(carpeta)
    placa = {}
    estado_placa = carpeta / ".halo" / "placa.json"
    if estado_placa.is_file():
        try:
            datos = json.loads(estado_placa.read_text(encoding="utf-8"))
            c = datos.get("contorno") or [0, 0, 0, 0]
            placa = {"etapa": datos.get("etapa"), "capas": datos.get("capas"),
                     "tamano": [round(c[2] - c[0], 2), round(c[3] - c[1], 2)], "planos": datos.get("planos", {})}
        except (OSError, ValueError, TypeError):
            placa = {}
    etapa = "pcb" if "pcb" in archivos and "esquematico" not in archivos else "esquematico"
    return {"proyecto": nombre_de(carpeta, archivos), "etapa": etapa,
            "archivo": archivos.get(etapa) or next(iter(archivos.values()), None), "archivos": archivos,
            "actividad": str(carpeta / ".halo" / "actividad.jsonl"), "halo": {"estado": "reposo", "texto": ""},
            "verificacion": None, "render3d": [], "placa": placa}


def es_proyecto(carpeta) -> bool:
    return Path(carpeta).is_dir() and bool(archivos_de(carpeta))


def pedir_apertura(carpeta, estado: Path = ESTADO) -> Path:
    """Un motor avisa que trabaja en esta carpeta: el Halo abrirá su pestaña si no estaba."""
    destino = Path(estado) / "abrir" / f"{clave(carpeta)}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"carpeta": identificador(carpeta)}), encoding="utf-8")
    return destino
