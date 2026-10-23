"""Servidor local del Halo: sirve la interfaz y empuja en vivo el trabajo de la IA.

    python -m halo.servidor [--puerto 8765]

Un servidor, varios proyectos: cada pestaña del Halo es una carpeta de proyecto con su estado en
<carpeta>/.halo/actual.json (lo escriben los motores y el editor):
    {"proyecto", "etapa", "archivos": {"esquematico": .kicad_sch, "pcb": .kicad_pcb},
     "actividad": .jsonl, "halo": {...}, "verificacion": {...}, "render3d": [png, ...]}
Las pestañas abiertas se recuerdan en build/halo/pestanas.json. Cuando cambia un archivo de un
proyecto o llega actividad, el estado se envía por Server-Sent Events, sin sondeo.
Sólo escucha en 127.0.0.1 y rechaza cualquier petición que no venga de su propia interfaz.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import editor
from . import fabricacion
from . import bloques
from . import proyectos
from .contexto import consultar, CAPACIDADES
from .kicad.vista import cargar_modelo
from .kicad.vista_pcb import cargar_placa
from .kicad.proyecto import revision
from .kicad.proyecto import ConflictoProyecto
from .simulacion import servicio as _sim_modulo
from .simulacion.servicio import SERVICIO as SIMULACION, dll_ngspice
from .simulacion.preparar import ErrorSimulacion
from .espacio3d import servicio as _e3_modulo
from .espacio3d.servicio import SERVICIO as ESPACIO3D
from .espacio3d.contrato import Error3D
from .chat.servicio import SERVICIO as CHAT, ErrorChat
from .documentos import servicio as DOCUMENTOS
from .documentos.servicio import ErrorDocumento
from .vigia import Observador

RAIZ = Path(__file__).resolve().parent
REPO = RAIZ.parent
UI = RAIZ / "ui"
ESTADO = REPO / "build" / "halo"
TIPOS = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
         ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon"}
CARGADORES = {".kicad_sch": cargar_modelo, ".kicad_pcb": cargar_placa}


class ErrorPestana(ValueError):
    pass


class Proyecto:
    """Una pestaña: la carpeta de un proyecto de KiCad con su estado, sus modelos y su bitácora."""

    def __init__(self, carpeta):
        self.carpeta = Path(carpeta).resolve()
        self.id = proyectos.identificador(self.carpeta)
        self.nombre = proyectos.nombre_de(self.carpeta)
        self.actual: dict = {}
        self.modelos: dict[str, dict] = {}
        self.actividad: list[dict] = []
        self.error: str | None = None
        self.version_modelos = 0
        self._mtimes: dict = {}
        self._previos: dict = {}
        self._act_archivo = None
        self._act_pos = 0
        self._docs_firma = None   # nombres y fechas de <carpeta>/docs: si cambian, la vista de documentos se refresca

    @property
    def estado_ruta(self) -> Path:
        return self.carpeta / ".halo" / "actual.json"

    def resumen(self) -> dict:
        halo = self.actual.get("halo") or {}
        return {"id": self.id, "nombre": self.actual.get("proyecto") or self.nombre, "carpeta": str(self.carpeta),
                "etapa": self.actual.get("etapa"), "estado": halo.get("estado", "reposo"), "texto": halo.get("texto", "")}


class Centro:
    def __init__(self, estado: Path = ESTADO):
        self.estado = Path(estado)
        self.cond = threading.Condition()
        self.version = 0
        self.proyectos: dict[str, Proyecto] = {}
        self.activo: str | None = None
        self.recientes: list[str] = []
        self.error: str | None = None
        self.latidos: dict[str, int] = {}
        self._senal = threading.Event()
        self._reintento = False
        self.observador = Observador(self._senal.set)

    # ------------------------------------------------------------ pestañas
    def proyecto(self, pid: str | None = None) -> Proyecto | None:
        if pid:
            p = self.proyectos.get(pid)
            if p is None:
                try:
                    p = self.proyectos.get(proyectos.identificador(pid))
                except OSError:
                    p = None
            return p
        return self.proyectos.get(self.activo) if self.activo else None

    def abrir(self, carpeta, activar: bool = True) -> Proyecto:
        carpeta = Path(carpeta)
        if not carpeta.is_dir():
            raise ErrorPestana(f"No existe la carpeta {carpeta}")
        if not proyectos.es_proyecto(carpeta):
            raise ErrorPestana(f"En {carpeta} no hay un esquemático ni una placa de KiCad")
        pid = proyectos.identificador(carpeta)
        with self.cond:
            p = self.proyectos.get(pid)
            nuevo = p is None
            if nuevo:
                p = Proyecto(carpeta)
                if not p.estado_ruta.is_file():
                    editor.escribir_atomico(p.estado_ruta, json.dumps(proyectos.estado_inicial(carpeta), ensure_ascii=False, indent=2))
                self.proyectos[pid] = p
            if activar or self.activo is None:
                self.activo = pid
            self.recientes = [pid] + [r for r in self.recientes if r != pid]
            self.recientes = self.recientes[:12]
            self._persistir()
            self.version += 1
            self.cond.notify_all()
        if nuevo:
            self._senal.set()
        return p

    def cerrar(self, pid: str) -> None:
        with self.cond:
            p = self.proyecto(pid)
            if p is None:
                raise ErrorPestana("Esa pestaña ya no está abierta")
            del self.proyectos[p.id]
            if self.activo == p.id:
                self.activo = next(iter(self.proyectos), None)
            self._persistir()
            self.version += 1
            self.cond.notify_all()

    def activar(self, pid: str) -> None:
        with self.cond:
            p = self.proyecto(pid)
            if p is None:
                raise ErrorPestana("Esa pestaña ya no está abierta")
            self.activo = p.id
            self._persistir()
            self.version += 1
            self.cond.notify_all()

    def reordenar(self, orden: list) -> None:
        with self.cond:
            ids = [self.proyecto(x).id for x in orden if self.proyecto(x)]
            self.proyectos = {**{i: self.proyectos[i] for i in ids}, **{i: p for i, p in self.proyectos.items() if i not in ids}}
            self._persistir()
            self.version += 1
            self.cond.notify_all()

    def pestanas(self) -> list[dict]:
        return [p.resumen() for p in self.proyectos.values()]

    def _persistir(self) -> None:
        editor.escribir_atomico(self.estado / "pestanas.json", json.dumps({
            "abiertas": [p.id for p in self.proyectos.values()], "activo": self.activo, "recientes": self.recientes},
            ensure_ascii=False, indent=2))

    def restaurar(self) -> None:
        """Al arrancar: las pestañas de la última vez; si es la primera vez, el proyecto del estado antiguo."""
        registro = self.estado / "pestanas.json"
        datos = {}
        if registro.is_file():
            try:
                datos = json.loads(registro.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                datos = {}
        else:
            viejo = self.estado / "actual.json"
            if viejo.is_file():
                try:
                    archivo = json.loads(viejo.read_text(encoding="utf-8")).get("archivo")
                    if archivo:
                        datos = {"abiertas": [str(Path(archivo).parent)]}
                except (OSError, ValueError):
                    datos = {}
        self.recientes = [r for r in datos.get("recientes", []) if isinstance(r, str)]
        for carpeta in datos.get("abiertas", []):
            try:
                self.abrir(carpeta, activar=False)
            except ErrorPestana:
                continue
        activo = datos.get("activo")
        if activo and self.proyecto(activo):
            self.activo = self.proyecto(activo).id

    # ------------------------------------------------------------ compatibilidad: el proyecto activo
    def _activo_o_nuevo(self, pista: dict | None = None) -> Proyecto:
        p = self.proyecto()
        if p is None:
            carpeta = (pista or {}).get("proyecto_id") or self.estado
            p = Proyecto.__new__(Proyecto)
            p.carpeta = Path(carpeta)
            p.id = str(p.carpeta)
            p.nombre = p.carpeta.name
            p.actual, p.modelos, p.actividad, p.error, p.version_modelos = {}, {}, [], None, 0
            p._mtimes, p._previos, p._act_archivo, p._act_pos = {}, {}, None, 0
            self.proyectos[p.id] = p
            self.activo = p.id
        return p

    @property
    def actual(self) -> dict:
        p = self.proyecto()
        return p.actual if p else {}

    @actual.setter
    def actual(self, valor: dict) -> None:
        self._activo_o_nuevo(valor).actual = valor

    @property
    def modelos(self) -> dict:
        p = self.proyecto()
        return p.modelos if p else {}

    @modelos.setter
    def modelos(self, valor: dict) -> None:
        self._activo_o_nuevo().modelos = valor

    @property
    def actividad(self) -> list:
        p = self.proyecto()
        return p.actividad if p else []

    # ------------------------------------------------------------ estado para la interfaz
    def notificar(self, servicio: str) -> None:
        """Un servicio del propio servidor (simulación, 3D, chat, documentos) cambió: se avisa a la interfaz por SSE."""
        with self.cond:
            self.latidos[servicio] = self.latidos.get(servicio, 0) + 1
            self.version += 1
            self.cond.notify_all()

    def instantanea(self, pid: str | None = None, con_modelos: bool = True) -> dict:
        p = self.proyecto(pid)
        data = {"version": self.version, "latidos": dict(self.latidos), "pestanas": self.pestanas(),
                "activo": self.activo, "recientes": list(self.recientes), "proyecto": p.id if p else None,
                "version_modelos": p.version_modelos if p else 0, "actual": p.actual if p else {},
                "actividad": p.actividad[-80:] if p else [], "error": (p.error if p else None) or self.error}
        if con_modelos:
            data["modelos"] = p.modelos if p else {}
            data["modelo"] = data["modelos"].get((p.actual if p else {}).get("etapa", ""))
        return data

    # ------------------------------------------------------------ vigilancia
    def _recientes(self, p: Proyecto, etapa: str, modelo: dict) -> None:
        firmas = {}
        if modelo["tipo"] == "esquematico":
            for s in modelo["simbolos"]:
                firmas[s["uuid"]] = (tuple(s["at"]), s["rot"], s["valor"])
            for c in modelo["cables"]:
                firmas[c["uuid"]] = tuple(map(tuple, c["pts"]))
        else:
            for h in modelo["huellas"]:
                firmas[h["uuid"]] = (tuple(h["at"]), h["ang"])
            for t in modelo["pistas"]:
                firmas[t["uuid"]] = (tuple(t["a"]), tuple(t["b"]))
        previos = p._previos.get(etapa)
        modelo["recientes"] = [k for k, v in firmas.items() if previos is not None and previos.get(k) != v]
        p._previos[etapa] = firmas

    def vigilar(self) -> None:
        # Sin sondeo: el sistema avisa cuando cambia un archivo de un proyecto o del estado, y entonces se da
        # una vuelta. Un error de lectura (archivo a medio escribir) no mata el hilo: se reintenta enseguida.
        while True:
            self._reintento = False
            try:
                self._vuelta()
            except Exception as exc:  # noqa: BLE001
                self.error = f"vigilancia: {type(exc).__name__}: {exc}"
                traceback.print_exc()
                self._reintento = True
            if self._reintento:
                time.sleep(0.25)
                continue
            self._senal.wait(self.observador.espera)
            if self._senal.is_set():
                time.sleep(0.08)  # dejar que termine la ráfaga de escritura antes de leer
                self._senal.clear()

    def _abrir_pedidas(self) -> None:
        """Un motor empezó a trabajar en una carpeta: su pestaña se abre y pasa al frente."""
        for aviso in sorted((self.estado / "abrir").glob("*.json")):
            try:
                carpeta = json.loads(aviso.read_text(encoding="utf-8")).get("carpeta")
                if carpeta:
                    self.abrir(carpeta, activar=True)
            except (OSError, ValueError, ErrorPestana):
                pass
            aviso.unlink(missing_ok=True)

    def _vuelta(self) -> None:
        self._abrir_pedidas()
        abiertos = list(self.proyectos.values())
        self.observador.observar([self.estado, *(p.carpeta for p in abiertos)])
        cambio = False
        for p in abiertos:
            cambio_p, modelos_nuevos = self._vuelta_proyecto(p)
            if cambio_p or modelos_nuevos:
                with self.cond:
                    self.version += 1
                    if modelos_nuevos:
                        p.version_modelos = self.version
                    self.cond.notify_all()
                cambio = True
        if cambio:
            self.error = None

    def _vuelta_proyecto(self, p: Proyecto) -> tuple[bool, bool]:
        cambio = modelos_nuevos = False
        docs = p.carpeta / DOCUMENTOS.DOCS
        try:
            firma_docs = tuple(sorted((q.name, q.stat().st_mtime_ns) for q in docs.iterdir() if q.is_file())) if docs.is_dir() else ()
        except OSError:
            firma_docs = p._docs_firma
        if firma_docs != p._docs_firma:
            primera = p._docs_firma is None
            p._docs_firma = firma_docs
            if not primera:
                self.notificar('documentos')
        modelos = dict(p.modelos)
        actividad = list(p.actividad)
        try:
            actual = json.loads(p.estado_ruta.read_text(encoding="utf-8")) if p.estado_ruta.is_file() else proyectos.estado_inicial(p.carpeta)
        except (OSError, json.JSONDecodeError):
            actual = p.actual
        actual = dict(actual)
        actual['revision'] = revision(p.carpeta)
        actual['proyecto_id'] = p.id
        if actual.get('verificacion') and actual['verificacion'].get('revision') != actual['revision']:
            actual['verificacion'] = None
        if actual != p.actual:
            cambio = True
        archivos = dict(actual.get("archivos") or {})
        if not archivos and actual.get("archivo"):
            archivos[actual.get("etapa", "esquematico")] = actual["archivo"]
        for etapa in list(modelos):
            if etapa not in archivos:
                del modelos[etapa]
                p._mtimes.pop(etapa, None)
                modelos_nuevos = True
        for etapa, archivo in archivos.items():
            ruta = Path(archivo)
            cargador = CARGADORES.get(ruta.suffix)
            if not cargador or not ruta.is_file():
                continue
            mtime = ruta.stat().st_mtime_ns
            if etapa == 'esquematico':
                fichas = ruta.parent / '.halo' / 'bloques.json'
                mtime = (mtime, fichas.stat().st_mtime_ns if fichas.is_file() else 0)
            clave = f"{etapa}:{archivo}"
            if p._mtimes.get(etapa) == (clave, mtime):
                continue
            try:
                modelo = cargador(ruta)
                if etapa == 'esquematico':
                    bloques.incorporar(modelo)
                self._recientes(p, etapa, modelo)
                modelos[etapa] = modelo
                p._mtimes[etapa] = (clave, mtime)
                p.error = None
                modelos_nuevos = True
            except Exception as exc:  # archivo a medio escribir: se reintenta enseguida
                p.error = f"{etapa}: {type(exc).__name__}: {exc}"
                self._reintento = True
        if revision(p.carpeta) != actual.get('revision'):
            p._mtimes.clear()   # cambió mientras se leía: se vuelve a cargar enseguida
            self._reintento = True
            return False, False
        act = actual.get("actividad")
        if act != p._act_archivo:
            p._act_archivo, p._act_pos, actividad = act, 0, []
            cambio = True
        if act and Path(act).is_file():
            tam = Path(act).stat().st_size
            if tam < p._act_pos:
                p._act_pos, actividad = 0, []
            with open(act, "rb") as fh:
                fh.seek(p._act_pos)
                data = fh.read()
            corte = data.rfind(b"\n")
            if corte >= 0:
                p._act_pos += corte + 1
                for linea in data[:corte].splitlines():
                    try:
                        actividad.append(json.loads(linea))
                    except json.JSONDecodeError:
                        continue
                actividad = actividad[-300:]
                cambio = True
        if cambio or modelos_nuevos:
            with self.cond:
                p.actual, p.modelos, p.actividad = actual, modelos, actividad
        return cambio, modelos_nuevos


CENTRO = Centro()
# Los servicios que viven dentro del servidor avisan al Centro cuando cambian; la interfaz no sondea.
_sim_modulo.AL_CAMBIAR = lambda: CENTRO.notificar('simulacion')
_e3_modulo.AL_CAMBIAR = lambda: CENTRO.notificar('espacio3d')
CHAT.al_cambiar = lambda: CENTRO.notificar('chat')


def _actual_de(pid: str | None) -> tuple[dict, dict]:
    with CENTRO.cond:
        p = CENTRO.proyecto(pid)
        return (dict(p.actual), dict(p.modelos)) if p else ({}, {})


def _carpeta_abierta(pid: str | None) -> Path:
    """La carpeta de una pestaña abierta; el chat sólo trabaja sobre proyectos que el Halo tiene a la vista."""
    with CENTRO.cond:
        p = CENTRO.proyecto(pid)
    if p is None:
        raise ErrorChat('Ese proyecto no está abierto en el Halo')
    return Path(p.carpeta)


class ServidorHalo(ThreadingHTTPServer):
    daemon_threads = True
    # La interfaz pide ~20 archivos a la vez al cargar. Con la cola por omisión (5), Windows rechazaba
    # conexiones (ERR_CONNECTION_REFUSED), se perdía un script y la ventana quedaba en café hasta pulsar F5.
    request_queue_size = 128

    def handle_error(self, request, client_address):
        """Una ventana que se cierra corta su conexión en vivo: es normal, no merece una traza en el registro."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Manejador(BaseHTTPRequestHandler):
    server_version = "Halo/0.3"

    def log_message(self, fmt, *args):
        pass

    def _enviar(self, codigo: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, codigo: int, datos) -> None:
        self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"), TIPOS[".json"])

    def _origen_local(self, escritura: bool) -> bool:
        """Sólo la propia interfaz, en este equipo. Host descarta el DNS rebinding; Origin y la cabecera propia
        (que obliga a un preflight que este servidor no contesta) impiden que otra web envíe órdenes."""
        puerto = self.server.server_port
        permitidos = {f'127.0.0.1:{puerto}', f'localhost:{puerto}', f'[::1]:{puerto}'}
        origen = self.headers.get('Origin')
        if self.headers.get('Host', '') not in permitidos or (origen and origen not in {'http://' + x for x in permitidos}):
            return False
        return not escritura or self.headers.get('X-Halo-Request') == '1'

    def do_GET(self):
        url = urlparse(self.path)
        ruta = url.path
        query = parse_qs(url.query)
        arg = lambda key: query.get(key, [''])[0]  # noqa: E731
        pid = arg('proyecto') or arg('proyecto_id') or None
        if not self._origen_local(escritura=False):
            return self._enviar(403, b'{"ok":false,"error":"Origen no permitido"}', TIPOS['.json'])
        if ruta == '/api/pestanas':
            with CENTRO.cond:
                return self._json(200, {'ok': True, 'pestanas': CENTRO.pestanas(), 'activo': CENTRO.activo,
                                        'recientes': list(CENTRO.recientes)})
        if ruta == '/api/chat':
            try:
                carpeta = _carpeta_abierta(pid)
                data = CHAT.conversacion(carpeta, arg('chat')) if arg('chat') else CHAT.listar(carpeta)
                return self._json(200, {'ok': True, **data})
            except (ErrorChat, OSError) as exc:
                return self._json(409, {'ok': False, 'error': str(exc)})
        if ruta in ('/api/documentos', '/api/documento', '/api/documento/archivo'):
            try:
                carpeta = _carpeta_abierta(pid)
                if ruta == '/api/documentos':
                    data = DOCUMENTOS.listar(carpeta)
                elif ruta == '/api/documento':
                    base = f"/api/documento/archivo?proyecto={quote(pid or '')}&ruta="
                    data = DOCUMENTOS.leer(carpeta, arg('nombre'), figuras_url=lambda r: base + quote(r))
                else:
                    archivo = DOCUMENTOS.archivo(carpeta, arg('ruta'))
                    tipo = TIPOS.get(archivo.suffix.lower(), {'.pdf': 'application/pdf', '.docx': 'application/octet-stream'}.get(archivo.suffix.lower(), 'application/octet-stream'))
                    return self._enviar(200, archivo.read_bytes(), tipo)
                return self._json(200, {'ok': True, **data})
            except (ErrorChat, ErrorDocumento, OSError) as exc:
                return self._json(409, {'ok': False, 'error': str(exc)})
        if ruta.startswith('/api/espacio3d'):
            try:
                if ruta == '/api/espacio3d/scene':
                    data = ESPACIO3D.escena(arg('project'), arg('revision'))
                elif ruta == '/api/espacio3d/artifact':
                    archivo = ESPACIO3D.archivo(arg('project'), arg('revision'), arg('name'))
                    content = archivo.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', f'attachment; filename="{archivo.name}"')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                elif ruta != '/api/espacio3d':
                    return self._enviar(404, b'no encontrado', 'text/plain')
                elif arg('job'):
                    data = ESPACIO3D.trabajo(arg('job'))
                elif arg('project'):
                    data = ESPACIO3D.documento(arg('project'))
                else:
                    data = ESPACIO3D.resumen()
                return self._json(200, data)
            except (ValueError, OSError) as exc:
                return self._json(409, {'ok': False, 'error': str(exc)})
        if ruta == '/api/bloques':
            try:
                actual, modelos = _actual_de(pid)
                if not actual or (pid and actual.get('proyecto_id') != CENTRO.proyecto(pid).id):
                    raise bloques.ErrorBloque('El proyecto activo ha cambiado')
                data = bloques.consultar(actual, modelos.get('esquematico', {}), arg('uuid') or None, SIMULACION.leer)
                return self._json(200, data)
            except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
                return self._json(409, {'ok': False, 'error': str(exc)})
        if ruta == '/api/fabricacion':
            try:
                data = fabricacion.consultar(arg('id'))
                if arg('descargar') == '1':
                    if not data.get('zip') or not data['vigente']:
                        raise fabricacion.ErrorFabricacion('No hay un ZIP vigente disponible para revisión')
                    archivo = fabricacion.SALIDA / data['id'] / 'GERBERS-PARA-REVISION.zip'
                    return self._enviar(200, archivo.read_bytes(), 'application/zip')
                return self._json(200, {'ok': True, **data})
            except (ValueError, OSError) as exc:
                return self._json(409, {'ok': False, 'error': str(exc)})
        if ruta == '/api/simulacion':
            try:
                actual, _ = _actual_de(pid)
                if arg('id'):
                    data = {'ok': True, **SIMULACION.leer(arg('id'), completo=arg('resumen') != '1')}
                else:
                    data = {'ok': True, 'motor_disponible': dll_ngspice().is_file(),
                            'trabajos': SIMULACION.listar(actual.get('proyecto_id')),
                            'config': SIMULACION.configuracion(actual)}
                return self._json(200, data)
            except (ValueError, OSError) as exc:
                return self._json(400, {'ok': False, 'error': str(exc)})
        if ruta == '/api/capacidades':
            return self._json(200, CAPACIDADES)
        if ruta == '/api/contexto':
            actual, modelos = _actual_de(pid)
            return self._json(200, consultar(actual, modelos, arg('ref') or None, arg('red') or None))
        if ruta == "/api/estado":
            with CENTRO.cond:
                data = CENTRO.instantanea(pid, True)
            return self._json(200, data)
        if ruta == "/api/eventos":
            return self._eventos(pid)
        if ruta == "/api/imagen":
            actual, _ = _actual_de(pid)
            try:
                archivo = Path(actual.get("render3d", [])[int(arg("i") or -1)])
            except (ValueError, IndexError):
                return self._enviar(404, b"sin imagen", "text/plain; charset=utf-8")
            if not archivo.is_file() or archivo.suffix not in (".png", ".jpg"):
                return self._enviar(404, b"sin imagen", "text/plain; charset=utf-8")
            return self._enviar(200, archivo.read_bytes(), TIPOS[archivo.suffix])
        nombre = "index.html" if ruta in ("/", "") else ruta.lstrip("/")
        archivo = (UI / nombre).resolve()
        if UI not in archivo.parents or not archivo.is_file():
            return self._enviar(404, b"no encontrado", "text/plain; charset=utf-8")
        # Los archivos de la interfaz (three.js pesa 2 MB) se revalidan con ETag: F5 o una ventana nueva
        # reciben un 304 vacío si no cambiaron, y un cambio en disco se ve igual al recargar.
        info = archivo.stat()
        etiqueta = f'"{info.st_mtime_ns:x}-{info.st_size:x}"'
        if self.headers.get("If-None-Match") == etiqueta:
            self.send_response(304)
            self.send_header("ETag", etiqueta)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        cuerpo = archivo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TIPOS.get(archivo.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("ETag", etiqueta)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):
        ruta = urlparse(self.path).path
        try:
            # Ninguna web ajena puede editar, simular, fabricar, modelar ni hablar con un agente.
            if not self._origen_local(escritura=True):
                return self._enviar(403, b'{"ok":false,"error":"Origen no permitido"}', TIPOS['.json'])
            largo = int(self.headers.get("Content-Length") or 0)
            if largo < 0 or largo > 1048576:
                return self._enviar(413, b'peticion demasiado grande', 'text/plain')
            peticion = json.loads(self.rfile.read(largo) or b"{}")
            if not isinstance(peticion, dict):
                raise editor.ErrorEdicion('La petición debe ser un objeto.')
            pid = peticion.get('proyecto_id')
            if ruta == '/api/pestanas':
                respuesta = self._pestanas(peticion)
            elif ruta == '/api/chat':
                respuesta = CHAT.operar({**peticion, 'proyecto_id': str(_carpeta_abierta(pid))})
            elif ruta == '/api/documentos':
                respuesta = DOCUMENTOS.operar({**peticion, 'proyecto_id': str(_carpeta_abierta(pid))})
                CENTRO.notificar('documentos')
            elif ruta == '/api/espacio3d':
                respuesta = ESPACIO3D.operar(peticion)
            elif ruta == "/api/editar":
                respuesta = editor.editar(peticion)
            elif ruta == "/api/render3d":
                respuesta = editor.render3d(peticion)
            elif ruta == '/api/fabricacion':
                actual, _ = _actual_de(pid)
                respuesta = fabricacion.preparar(actual, peticion)
            elif ruta == '/api/simulacion':
                actual, _ = _actual_de(pid)
                op = peticion.get('op')
                if op == 'preparar':
                    respuesta = SIMULACION.preparar(actual, peticion)
                elif op == 'ejecutar':
                    respuesta = SIMULACION.ejecutar(peticion.get('id'), peticion.get('timeout', 60))
                elif op == 'cancelar':
                    respuesta = SIMULACION.cancelar(peticion.get('id'))
                elif op == 'comparar':
                    respuesta = SIMULACION.comparar(peticion.get('id'), peticion.get('base'))
                elif op == 'inventario':
                    respuesta = SIMULACION.inventario(actual, peticion)
                elif op == 'configurar':
                    respuesta = SIMULACION.configurar(actual, peticion)
                else:
                    raise ErrorSimulacion('Operación de simulación desconocida')
            else:
                return self._enviar(404, b"no encontrado", "text/plain; charset=utf-8")
            codigo = 200
        except editor.ErrorEdicion as exc:
            respuesta, codigo = {"ok": False, "error": str(exc)}, 409
        except (Error3D, ErrorSimulacion, ConflictoProyecto, fabricacion.ErrorFabricacion,
                ErrorPestana, ErrorChat, ErrorDocumento) as exc:
            respuesta, codigo = {"ok": False, "error": str(exc)}, 409
        except (ValueError, TypeError, KeyError) as exc:
            respuesta, codigo = {"ok": False, "error": 'Petición inválida: ' + str(exc)}, 400
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            respuesta, codigo = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500
        return self._json(codigo, respuesta)

    def _pestanas(self, peticion: dict) -> dict:
        op = peticion.get('op')
        if op == 'abrir':
            carpeta = str(peticion.get('carpeta') or '').strip()
            if not carpeta:
                raise ErrorPestana('Indica la carpeta del proyecto')
            p = CENTRO.abrir(carpeta, activar=True)
            resultado = {'proyecto': p.id}
        elif op == 'cerrar':
            CENTRO.cerrar(str(peticion.get('proyecto_id') or ''))
            resultado = {}
        elif op == 'activar':
            CENTRO.activar(str(peticion.get('proyecto_id') or ''))
            resultado = {}
        elif op == 'reordenar':
            CENTRO.reordenar(list(peticion.get('orden') or []))
            resultado = {}
        else:
            raise ErrorPestana('Operación de pestañas desconocida')
        with CENTRO.cond:
            return {'ok': True, **resultado, 'pestanas': CENTRO.pestanas(), 'activo': CENTRO.activo,
                    'recientes': list(CENTRO.recientes)}

    def _eventos(self, pid: str | None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        visto, visto_modelos = -1, -1
        try:
            while True:
                with CENTRO.cond:
                    if CENTRO.version == visto:
                        CENTRO.cond.wait(timeout=15)
                    if CENTRO.version == visto:
                        paquete = None
                    else:
                        p = CENTRO.proyecto(pid)
                        vm = p.version_modelos if p else 0
                        paquete = CENTRO.instantanea(pid, vm != visto_modelos)
                        visto, visto_modelos = CENTRO.version, vm
                if paquete is None:
                    self.wfile.write(b": latido\n\n")
                else:
                    datos = json.dumps(paquete, ensure_ascii=False)
                    self.wfile.write(f"event: estado\ndata: {datos}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--puerto", type=int, default=8765)
    args = parser.parse_args()
    ESTADO.mkdir(parents=True, exist_ok=True)
    SIMULACION.recuperar()
    CENTRO.restaurar()
    threading.Thread(target=CENTRO.vigilar, daemon=True).start()
    servidor = ServidorHalo(("127.0.0.1", args.puerto), Manejador)
    servidor.daemon_threads = True
    print(f"[halo] http://127.0.0.1:{args.puerto}/", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
