"""Sesión en vivo: lo que la IA está haciendo, visible en el Halo al instante.

Escribe <carpeta del proyecto>/.halo/actual.json (proyecto, etapa, archivos de cada etapa, estado
del halo, verificación, vistas 3D y datos de la placa) y una bitácora .jsonl por proyecto con
recibos crecientes. Con continuar=True una etapa nueva sigue la bitácora de la anterior. Al
empezar deja un aviso en build/halo/abrir/ para que el Halo abra la pestaña del proyecto.
Compatible con el Python de KiCad (3.11) y con el del sistema.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from .proyecto import atomico, revision

REPO = Path(__file__).resolve().parents[2]
ESTADO = REPO / "build" / "halo"
ACTUAL = ESTADO / "actual.json"   # ya no se usa: el estado es por proyecto (se conserva por compatibilidad)


def _pedir_apertura(carpeta: Path) -> None:
    try:
        from ..proyectos import pedir_apertura
        pedir_apertura(carpeta, ESTADO)
    except Exception:  # noqa: BLE001 - un aviso fallido no debe detener a un motor
        pass


def escribir_atomico(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(50):
        try:
            atomico(ruta, texto.encode('utf-8'))
            return
        except PermissionError:
            time.sleep(0.02)
    raise PermissionError(f"no se pudo reemplazar {ruta}")


class Sesion:
    def __init__(self, proyecto: str, archivo: Path, etapa: str = "esquematico", pausa: float = 0,
                 archivos: dict | None = None, continuar: bool = False):
        self.pausa = pausa
        self.archivo = Path(archivo)
        self.bitacora = self.archivo.parent / ".halo" / "actividad.jsonl"
        self.bitacora.parent.mkdir(parents=True, exist_ok=True)
        previo = {}
        if continuar and self.bitacora.is_file():
            with open(self.bitacora, encoding="utf-8") as fh:
                self.recibo = max([json.loads(l).get("recibo") or 0 for l in fh if l.strip()] or [0])
            try:
                previo = json.loads(self.actual_ruta.read_text(encoding="utf-8")) if self.actual_ruta.is_file() else {}
            except (OSError, json.JSONDecodeError):
                previo = {}
            if previo.get("actividad") != str(self.bitacora):
                previo = {}
        else:
            self.bitacora.write_text("", encoding="utf-8")
            self.recibo = 0
        _pedir_apertura(self.archivo.parent)
        archivos = archivos or {etapa: self.archivo}
        self.datos = {"proyecto": proyecto, "etapa": etapa, "archivo": str(self.archivo),
                      "archivos": {k: str(v) for k, v in archivos.items()},
                      "actividad": str(self.bitacora), "halo": {"estado": "pensando", "texto": "preparando"},
                      "verificacion": previo.get("verificacion"), "render3d": previo.get("render3d", []),
                      "placa": previo.get("placa", {})}
        self._publicar()

    @property
    def actual_ruta(self) -> Path:
        return self.archivo.parent / ".halo" / "actual.json"

    def _publicar(self) -> None:
        self.datos['revision'] = revision(self.archivo.parent)
        if self.datos.get('verificacion') and self.datos['verificacion'].get('revision') != self.datos['revision']:
            self.datos['verificacion'] = None
        escribir_atomico(self.actual_ruta, json.dumps(self.datos, ensure_ascii=False, indent=2))

    def halo(self, estado: str, texto: str = "") -> None:
        self.datos["halo"] = {"estado": estado, "texto": texto}
        self._publicar()

    def marcar(self, **campos) -> None:
        """Datos de etapa para el panel, p. ej. placa={"etapa": "acomodada", "capas": 4}."""
        for clave, valor in campos.items():
            if isinstance(valor, dict) and isinstance(self.datos.get(clave), dict):
                self.datos[clave].update(valor)
            else:
                self.datos[clave] = valor
        self._publicar()

    def evento(self, texto: str, op: str = "", nivel: str = "", **extra) -> int:
        self.recibo += 1
        linea = {"t": int(time.time() * 1000), "recibo": self.recibo, "op": op, "texto": texto, "nivel": nivel, **extra}
        with open(self.bitacora, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(linea, ensure_ascii=False) + "\n")
        return self.recibo

    def verificacion(self, datos: dict) -> None:
        self.datos["verificacion"] = {**datos, "cuando": int(time.time() * 1000), 'revision': revision(self.archivo.parent)}
        self._publicar()

    def render3d(self, rutas) -> None:
        self.datos["render3d"] = [str(r) for r in rutas]
        self._publicar()

    def esperar(self) -> None:
        if self.pausa > 0:
            time.sleep(self.pausa)
