"""Fresh, validated KiCad reports. A failed command never means a clean design."""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path


def ejecutar(args: list[str], salida: Path, runner) -> Path:
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    nueva = salida.with_name(f"{salida.stem}-{uuid.uuid4().hex}{salida.suffix}")
    comando = [str(nueva) if a == str(salida) else a for a in args]
    try:
        resultado = runner(comando)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"KiCad no pudo verificar: {exc}") from exc
    if resultado.returncode != 0 or not nueva.is_file():
        detalle = (resultado.stderr or resultado.stdout or "sin informe nuevo")[-1500:]
        raise RuntimeError(f"KiCad no verificó el diseño (código {resultado.returncode}): {detalle}")
    return nueva


def informe_json(args: list[str], salida: Path, runner, campos: tuple[str, ...]) -> dict:
    nueva = ejecutar(args, salida, runner)
    try:
        data = json.loads(nueva.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(data.get(c), list) for c in campos):
            raise ValueError(f"faltan listas obligatorias: {', '.join(campos)}")
        if "sheets" in campos:
            if any(not isinstance(s, dict) or not isinstance(s.get("violations"), list) for s in data['sheets']):
                raise ValueError("hojas ERC incompletas")
        if any(not isinstance(v, dict) for c in campos for v in data[c]):
            raise ValueError("elementos de informe inválidos")
        violaciones = data.get('violations', []) + [v for s in data.get('sheets', []) for v in s['violations']]
        if any(not isinstance(v, dict) or v.get('severity') not in ('error', 'warning', 'ignore') for v in violaciones):
            raise ValueError('severidades ausentes o desconocidas')
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Informe de KiCad inválido: {exc}") from exc
    # Publish only a fully validated new report; the old one cannot be consumed on failure.
    nueva.replace(salida)
    return data
