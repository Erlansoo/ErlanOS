"""Lectura de la conversación oficial de Claude Code (~/.claude/projects/<carpeta>/<sesión>.jsonl).

Es la única fuente de verdad del historial: lo que se habló desde el Halo, desde una consola o desde el
celular queda ahí. Aquí se convierte en una lista de piezas para dibujar: usuario, asistente,
herramienta, resultado. Nada se interpreta como instrucción; todo se muestra como texto.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

HOME = Path.home()
# HALO_TRANSCRIPCIONES sólo se usa con un doble del CLI (pruebas, demostraciones); Claude Code escribe en ~/.claude.
TRANSCRIPCIONES = Path(os.environ.get("HALO_TRANSCRIPCIONES") or HOME / ".claude" / "projects")
_cache: dict[str, tuple[int, list]] = {}

RESUMEN_ENTRADA = {
    "Bash": "command", "PowerShell": "command", "Read": "file_path", "Edit": "file_path", "Write": "file_path",
    "MultiEdit": "file_path", "Grep": "pattern", "Glob": "pattern", "WebFetch": "url", "WebSearch": "query",
    "Agent": "description", "Task": "description", "Skill": "skill", "NotebookEdit": "notebook_path",
}


def archivo_de(sesion: str, raiz: Path = TRANSCRIPCIONES) -> Path | None:
    if not sesion:
        return None
    candidatos = list(Path(raiz).glob(f"*/{sesion}.jsonl"))
    return max(candidatos, key=lambda p: p.stat().st_mtime) if candidatos else None


def resumir_entrada(nombre: str, entrada) -> str:
    if isinstance(entrada, dict):
        clave = RESUMEN_ENTRADA.get(nombre)
        if clave and isinstance(entrada.get(clave), str):
            return entrada[clave]
        return json.dumps(entrada, ensure_ascii=False)[:400]
    return str(entrada)[:400]


def texto_de(contenido, limite: int = 1200) -> str:
    if isinstance(contenido, str):
        texto = contenido
    elif isinstance(contenido, list):
        partes = []
        for b in contenido:
            if isinstance(b, dict) and b.get("type") == "text":
                partes.append(str(b.get("text", "")))
            elif isinstance(b, str):
                partes.append(b)
        texto = "\n".join(partes)
    else:
        texto = "" if contenido is None else str(contenido)
    return texto if len(texto) <= limite else texto[:limite] + f"\n… ({len(texto) - limite} caracteres más)"


def hora_local(marca: str) -> str:
    """Claude Code anota las horas en UTC (…Z); en pantalla van en la hora del equipo, como las del turno en vivo."""
    if not marca:
        return ""
    try:
        return datetime.fromisoformat(marca.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return marca


def piezas_de_mensaje(tipo: str, mensaje: dict, hora: str = "") -> list[dict]:
    """Piezas dibujables de un mensaje con el formato de la API de Anthropic (usuario o asistente)."""
    hora = hora_local(hora)
    piezas = []
    contenido = (mensaje or {}).get("content")
    if isinstance(contenido, str):
        if contenido.strip():
            piezas.append({"tipo": "usuario" if tipo == "user" else "asistente", "texto": contenido, "hora": hora})
        return piezas
    for b in contenido or []:
        if not isinstance(b, dict):
            continue
        clase = b.get("type")
        if clase == "text" and str(b.get("text", "")).strip():
            piezas.append({"tipo": "usuario" if tipo == "user" else "asistente", "texto": str(b["text"]), "hora": hora})
        elif clase == "tool_use":
            piezas.append({"tipo": "herramienta", "id": b.get("id"), "nombre": str(b.get("name", "?")),
                           "entrada": resumir_entrada(str(b.get("name", "")), b.get("input")), "hora": hora})
        elif clase == "tool_result":
            piezas.append({"tipo": "resultado", "de": b.get("tool_use_id"), "error": bool(b.get("is_error")),
                           "texto": texto_de(b.get("content"), 600), "hora": hora})
    return piezas


def leer(sesion: str, raiz: Path = TRANSCRIPCIONES) -> list[dict]:
    """Historial de una sesión como piezas; se recuerda mientras el archivo no cambie."""
    archivo = archivo_de(sesion, raiz)
    if archivo is None:
        return []
    firma = archivo.stat().st_mtime_ns
    previo = _cache.get(str(archivo))
    if previo and previo[0] == firma:
        return previo[1]
    piezas: list[dict] = []
    with open(archivo, encoding="utf-8", errors="replace") as fh:
        for linea in fh:
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
                continue
            piezas.extend(piezas_de_mensaje(d["type"], d.get("message") or {}, str(d.get("timestamp", ""))))
    _cache[str(archivo)] = (firma, piezas)
    return piezas
