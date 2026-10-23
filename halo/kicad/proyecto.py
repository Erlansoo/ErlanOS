"""Propiedad del proyecto y revisiones optimistas, compartidas por la interfaz y los motores.

revision()   SHA-256 del contenido de los archivos del proyecto. Cada archivo se vuelve a leer
             sólo si cambió su tamaño o su fecha: el servidor puede consultarla varias veces por
             segundo sin releer megabytes.
bloquear()   cerrojo por hilo y por proceso sobre .halo/escritura.lock; el sistema lo suelta si
             el proceso muere, el archivo vacío que queda no bloquea nada.
operacion()  decorador de las operaciones públicas de los motores. Comprueba que el proyecto no
             cambió por fuera y, si la operación falla:
               - sin haber tocado el disco (un rechazo normal: choque, sin ruta, red inexistente),
                 el motor vuelve a su estado en memoria previo y sigue usable;
               - con archivos ya escritos, los restaura, el motor se recarga del disco y sigue;
               - si el motor no sabe recargarse, la instancia queda invalidada: hay que reabrir.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


class ConflictoProyecto(RuntimeError):
    pass


_mutex = threading.Lock()
_locks: dict = {}
_local = threading.local()
_digestos: dict[str, tuple[tuple[int, int], bytes]] = {}


def archivos(carpeta):
    carpeta = Path(carpeta).resolve()
    rutas = {p for ext in ('*.kicad_sch', '*.kicad_pcb', '*.kicad_pro', '*.kicad_dru', '*.kicad_sym')
             for p in carpeta.glob(ext)}
    rutas.update(carpeta / n for n in ('.halo/placa.json', '.halo/intencion.json', 'sym-lib-table', 'fp-lib-table'))
    return sorted(p for p in rutas if p.is_file())


def _digesto(p: Path) -> bytes:
    """SHA-256 de un archivo, recordado mientras no cambien su tamaño ni su fecha."""
    st = p.stat()
    firma = (st.st_size, st.st_mtime_ns)
    clave = os.path.normcase(str(p))
    with _mutex:
        previo = _digestos.get(clave)
    if previo and previo[0] == firma:
        return previo[1]
    d = hashlib.sha256(p.read_bytes()).digest()
    with _mutex:
        _digestos[clave] = (firma, d)
    return d


def revision(carpeta):
    carpeta = Path(carpeta).resolve()
    h = hashlib.sha256()
    for p in archivos(carpeta):
        try:
            d = _digesto(p)
        except FileNotFoundError:
            continue  # borrado entre el listado y la lectura
        h.update(p.relative_to(carpeta).as_posix().encode('utf-8') + b'\0')
        h.update(d)
    return h.hexdigest()


def comprobar(carpeta, esperada):
    actual = revision(carpeta)
    if not isinstance(esperada, str) or actual != esperada:
        raise ConflictoProyecto('El proyecto cambió. Actualiza el estado antes de repetir la operación.')
    return actual


def atomico(ruta, datos):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_bytes(datos)
        for intento in range(50):
            try:
                os.replace(tmp, ruta)
                break
            except PermissionError:  # otro proceso lo tiene abierto un instante (Windows)
                if intento == 49:
                    raise
                time.sleep(0.02)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def bloquear(carpeta):
    carpeta = Path(carpeta).resolve()
    key = os.path.normcase(str(carpeta))
    with _mutex:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        activos = getattr(_local, 'activos', None)
        if activos is None:
            activos = _local.activos = set()
        if key in activos:
            yield
            return
        ruta = carpeta / '.halo' / 'escritura.lock'
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open('a+b') as f:
            f.seek(0, 2)
            if f.tell() == 0:
                f.write(b'0')
                f.flush()
            f.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ConflictoProyecto('Otro proceso está editando este proyecto; vuelve a intentarlo al terminar.') from exc
            activos.add(key)
            try:
                yield
            finally:
                activos.remove(key)
                f.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_UN)


def captura(carpeta):
    return {p: p.read_bytes() for p in archivos(carpeta)}


def restaurar(carpeta, copia):
    # Sólo los miembros del proyecto, dentro de la carpeta ya elegida.
    for p in archivos(carpeta):
        if p not in copia:
            p.unlink()
    for p, datos in copia.items():
        atomico(p, datos)


def operacion(fn):
    @wraps(fn)
    def protegida(self, *args, **kwargs):
        with bloquear(self.carpeta):
            if getattr(self, '_operacion_activa', False):
                return fn(self, *args, **kwargs)
            if getattr(self, '_invalido', False):
                raise ConflictoProyecto('La operación anterior falló a medias; vuelve a abrir el proyecto.')
            comprobar(self.carpeta, self._revision_proyecto)
            copia = captura(self.carpeta)
            marcar = getattr(self, '_marca_memoria', None)
            marca = marcar() if marcar is not None else None
            self._operacion_activa = True
            try:
                if getattr(self, 'etapa', None) == 'verificada' and fn.__name__ not in ('verificar', 'guardar'):
                    self.etapa = 'ruteada'
                return fn(self, *args, **kwargs)
            except BaseException:
                cambiados = captura(self.carpeta) != copia
                if cambiados:
                    self._invalido = True
                    restaurar(self.carpeta, copia)
                    if getattr(self, 'sesion', None):
                        self.sesion.halo('error', 'operación fallida; proyecto restaurado')
                volver = getattr(self, '_volver_memoria', None)
                if volver is not None:
                    try:
                        volver(marca, cambiados)
                        self._invalido = False
                    except Exception:
                        self._invalido = True
                raise
            finally:
                self._operacion_activa = False
                self._revision_proyecto = revision(self.carpeta)
    return protegida


def proteger_motor(cls, nombres):
    for nombre in nombres:
        setattr(cls, nombre, operacion(getattr(cls, nombre)))
    abrir = cls.abrir.__func__

    @classmethod
    @wraps(abrir)
    def abrir_protegido(tipo, *args, **kwargs):
        # Abrir no toma el cerrojo: consultar (estado, perfil, zonas) o verificar desde el Halo debe poder
        # convivir con una operación larga de otro proceso. Basta con que el proyecto no cambie mientras
        # se lee; si cambia, se vuelve a leer.
        carpeta = args[0] if args else kwargs.get('ruta', kwargs.get('carpeta'))
        p = Path(carpeta)
        directorio = p.parent if p.suffix == '.kicad_sch' else p
        for intento in range(4):
            antes = revision(directorio)
            objeto = abrir(tipo, *args, **kwargs)
            objeto._revision_proyecto = revision(directorio)
            if objeto._revision_proyecto == antes:
                return objeto
            time.sleep(0.1 * (intento + 1))
        raise ConflictoProyecto('El proyecto está cambiando mientras se abre; vuelve a intentarlo.')
    cls.abrir = abrir_protegido
