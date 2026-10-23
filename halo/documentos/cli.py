"""Documentación desde la terminal (la IA la usa igual que la interfaz):

    python -m halo.documentos.cli <carpeta> listar
    python -m halo.documentos.cli <carpeta> guia
    python -m halo.documentos.cli <carpeta> memoria [--nombre memoria.md] [--reemplazar]
    python -m halo.documentos.cli <carpeta> informe [--nombre informe.tex] [--reemplazar]
    python -m halo.documentos.cli <carpeta> actualizar <documento>
    python -m halo.documentos.cli <carpeta> compilar <documento.tex>
    python -m halo.documentos.cli <carpeta> convertir <documento> <docx|pdf|html|tex|md|odt>
    python -m halo.documentos.cli <carpeta> figuras

Todo responde en JSON; los errores salen por stderr también en JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import servicio


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Documentación del proyecto en el Halo")
    p.add_argument("carpeta")
    sub = p.add_subparsers(dest="orden", required=True)
    sub.add_parser("listar")
    sub.add_parser("guia")
    for plantilla in ("memoria", "informe"):
        s = sub.add_parser(plantilla)
        s.add_argument("--nombre")
        s.add_argument("--reemplazar", action="store_true")
    sub.add_parser("actualizar").add_argument("documento")
    sub.add_parser("compilar").add_argument("documento")
    c = sub.add_parser("convertir")
    c.add_argument("documento")
    c.add_argument("formato")
    sub.add_parser("figuras")
    a = p.parse_args(argv)
    carpeta = Path(a.carpeta).resolve()
    try:
        if a.orden == "listar":
            r = servicio.listar(carpeta)
        elif a.orden == "guia":
            r = servicio.guia(carpeta)
        elif a.orden in ("memoria", "informe"):
            r = servicio.crear(carpeta, a.orden, a.nombre, a.reemplazar)
        elif a.orden == "actualizar":
            r = servicio.actualizar(carpeta, a.documento)
        elif a.orden == "compilar":
            r = servicio.compilar(carpeta, a.documento)
        elif a.orden == "convertir":
            r = servicio.convertir(carpeta, a.documento, a.formato)
        else:
            r = servicio.figuras(carpeta)
    except servicio.ErrorDocumento as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **r}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
