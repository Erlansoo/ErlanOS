"""Fotografía una página del Halo con Edge sin ventana, por el protocolo de DevTools, sin bibliotecas.

    python -m halo.lanzador.capturar "http://127.0.0.1:8765/?proyecto=...&chat=1" docs/evidence/x.png [--espera 4] [--ancho 1440] [--alto 900]

Edge se lanza con --headless=new y --remote-debugging-port; se le habla por WebSocket (cliente mínimo
propio) para fijar el tamaño, navegar, esperar a que el servidor mande el estado y capturar en PNG.
No usa --screenshot ni --virtual-time-budget: la conexión en vivo (SSE) del Halo los deja colgados.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PERFIL = REPO / "build" / "halo" / "edge-headless"
EDGES = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
         Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe"]


class Devtools:
    """Lo justo del protocolo WebSocket (RFC 6455) para hablar con una página de Edge."""

    def __init__(self, url: str):
        _, resto = url.split("://", 1)
        host, ruta = resto.split("/", 1)
        nombre, puerto = host.split(":")
        self.s = socket.create_connection((nombre, int(puerto)), timeout=30)
        clave = base64.b64encode(secrets.token_bytes(16)).decode()
        self.s.sendall((f"GET /{ruta} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {clave}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        respuesta = b""
        while b"\r\n\r\n" not in respuesta:
            respuesta += self.s.recv(4096)
        if b" 101 " not in respuesta.split(b"\r\n", 1)[0]:
            raise RuntimeError("Edge no aceptó la conexión WebSocket")
        self.n = 0

    def _leer(self, largo: int) -> bytes:
        datos = b""
        while len(datos) < largo:
            trozo = self.s.recv(largo - len(datos))
            if not trozo:
                raise RuntimeError("Edge cerró la conexión")
            datos += trozo
        return datos

    def _enviar(self, texto: str) -> None:
        carga = texto.encode()
        cabeza = bytes([0x81])
        if len(carga) < 126:
            cabeza += bytes([0x80 | len(carga)])
        elif len(carga) < 65536:
            cabeza += bytes([0x80 | 126]) + struct.pack(">H", len(carga))
        else:
            cabeza += bytes([0x80 | 127]) + struct.pack(">Q", len(carga))
        mascara = secrets.token_bytes(4)
        self.s.sendall(cabeza + mascara + bytes(b ^ mascara[i % 4] for i, b in enumerate(carga)))

    def _recibir(self) -> str:
        while True:
            b0, b1 = self._leer(2)
            largo = b1 & 0x7F
            if largo == 126:
                largo = struct.unpack(">H", self._leer(2))[0]
            elif largo == 127:
                largo = struct.unpack(">Q", self._leer(8))[0]
            carga = self._leer(largo)
            opcode = b0 & 0x0F
            if opcode == 0x9:                         # ping → pong
                self.s.sendall(bytes([0x8A, 0x80]) + secrets.token_bytes(4))
            elif opcode in (0x1, 0x0):
                return carga.decode("utf-8", "replace")
            elif opcode == 0x8:
                raise RuntimeError("Edge cerró la conexión")

    def llamar(self, metodo: str, **params):
        self.n += 1
        self._enviar(json.dumps({"id": self.n, "method": metodo, "params": params}))
        while True:
            mensaje = json.loads(self._recibir())
            if mensaje.get("id") == self.n:
                if "error" in mensaje:
                    raise RuntimeError(f"{metodo}: {mensaje['error']}")
                return mensaje.get("result", {})


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def capturar(url: str, salida: Path, ancho: int = 1440, alto: int = 900, espera: float = 4.0) -> Path:
    edge = next((e for e in EDGES if e.is_file()), None)
    if edge is None:
        raise SystemExit("No encuentro msedge.exe")
    puerto = puerto_libre()
    perfil = PERFIL.with_name(f"{PERFIL.name}-{os.getpid()}")   # perfil propio por ejecución: nunca choca con un Edge anterior
    perfil.mkdir(parents=True, exist_ok=True)
    proceso = subprocess.Popen([str(edge), "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                                f"--remote-debugging-port={puerto}", f"--user-data-dir={perfil}",
                                f"--window-size={ancho},{alto}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    navegador = None   # dirección WebSocket del navegador entero, para cerrarlo limpio al final
    try:
        objetivo = None
        for _ in range(60):
            time.sleep(0.25)
            try:
                c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=2)
                c.request("GET", "/json/list")
                paginas = [t for t in json.loads(c.getresponse().read()) if t.get("type") == "page"]
                if paginas:
                    objetivo = paginas[0]
                    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=2)
                    c.request("GET", "/json/version")
                    navegador = json.loads(c.getresponse().read()).get("webSocketDebuggerUrl")
                    break
            except (OSError, ValueError):
                continue
        if objetivo is None:
            raise RuntimeError("Edge no abrió su puerto de depuración")
        dev = Devtools(objetivo["webSocketDebuggerUrl"])
        dev.llamar("Emulation.setDeviceMetricsOverride", width=ancho, height=alto, deviceScaleFactor=1, mobile=False)
        dev.llamar("Page.navigate", url=url)
        time.sleep(espera)   # el estado llega por SSE poco después de cargar
        datos = dev.llamar("Page.captureScreenshot", format="png")["data"]
        salida = Path(salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_bytes(base64.b64decode(datos))
        return salida
    finally:
        # Primero se le pide a Edge que cierre (así suelta el perfil); el lanzador que arrancamos suele
        # terminar antes que el navegador real, por eso el remate es por línea de órdenes, no por PID.
        if navegador:
            try:
                Devtools(navegador).llamar("Browser.close")
            except (OSError, RuntimeError, ValueError):
                pass
        try:
            proceso.wait(5)
        except subprocess.TimeoutExpired:
            proceso.kill()
        for _ in range(20):
            time.sleep(0.25)
            if not _edge_vivo(perfil):
                break
        else:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object {{ $_.CommandLine -match '{perfil.name}' }} "
                            "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(0.5)
        shutil.rmtree(perfil, ignore_errors=True)


def _edge_vivo(perfil: Path) -> bool:
    """¿Queda algún proceso de Edge usando este perfil? (tasklist es rápido y viene con Windows)."""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/FO", "CSV", "/V"], capture_output=True, text=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    # tasklist no muestra la línea de órdenes; se comprueba por el candado que Edge mantiene en el perfil
    candado = perfil / "lockfile"
    if not candado.exists():
        return False
    try:
        os.rename(candado, candado)   # con Edge vivo el candado está en uso y renombrarlo falla
        return False
    except OSError:
        return "msedge.exe" in r.stdout


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("url")
    p.add_argument("salida")
    p.add_argument("--ancho", type=int, default=1440)
    p.add_argument("--alto", type=int, default=900)
    p.add_argument("--espera", type=float, default=4.0)
    a = p.parse_args()
    ruta = capturar(a.url, Path(a.salida), a.ancho, a.alto, a.espera)
    print(f"Guardado {ruta} ({ruta.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
