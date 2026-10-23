"""Proyectos 3D persistentes. Publicación atómica tras validar motores aislados."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from .contrato import CATALOGO, Error3D, MAX_TRIANGULOS, ejemplo, validar_operaciones

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / 'data' / 'halo3d'
WORKERS = Path(__file__).resolve().parent
HEX = re.compile(r'^[0-9a-f]{32}$')


def leer(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


AL_CAMBIAR = None   # lo fija el servidor: avisa a la interfaz cada vez que se publica un cambio
_MOTORES = {'cuando': 0.0, 'valor': None}


def guardar(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    if AL_CAMBIAR:
        AL_CAMBIAR()


class Bloqueo:
    """El SO libera el bloqueo aunque el servidor termine abruptamente."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, 'a+b')
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise Error3D('Hay una operación 3D en curso. Espera o cancélala.')

    def close(self):
        self.file.close()


def motores():
    # Buscar los ejecutables cuesta varios globs; se recuerda un minuto.
    if _MOTORES['valor'] is not None and time.monotonic() - _MOTORES['cuando'] < 60:
        return copy.deepcopy(_MOTORES['valor'])
    _MOTORES.update(cuando=time.monotonic(), valor=_motores())
    return copy.deepcopy(_MOTORES['valor'])


def _motores():
    fc = os.environ.get('HALO_FREECAD_PYTHON')
    if not fc:
        candidates = sorted((REPO / 'build' / 'halo-motores' / 'freecad').glob('*/bin/python.exe'))
        fc = str(candidates[-1]) if candidates else ''
    bm = os.environ.get('HALO_BLENDER', 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe')
    return {'freecad': {'available': bool(fc and Path(fc).is_file()), 'executable': fc},
            'bmesh': {'available': Path(bm).is_file(), 'executable': bm}}


class Servicio3D:
    def __init__(self, base=BASE):
        self.base = Path(base)
        self.mutex = threading.RLock()
        self.cancels = {}
        self.base.mkdir(parents=True, exist_ok=True)
        # A previous server can have died after creating a job but before publishing.
        try:
            lock = Bloqueo(self.base / 'trabajo.lock')
        except Error3D:
            return
        try:
            for path in (self.base / 'jobs').glob('*.json'):
                meta = leer(path)
                if meta['status'] in ('queued', 'running', 'cancelling'):
                    meta.update(status='interrupted', error='El servicio se reinició antes de publicar el resultado.')
                    guardar(path, meta)
        finally:
            lock.close()

    def _project(self, pid):
        if not isinstance(pid, str) or not HEX.fullmatch(pid):
            raise Error3D('Identificador de proyecto inválido')
        path = self.base / 'projects' / pid
        if not (path / 'project.json').is_file():
            raise Error3D('Proyecto 3D no encontrado')
        return path

    def _job(self, jid):
        if not isinstance(jid, str) or not HEX.fullmatch(jid):
            raise Error3D('Identificador de trabajo inválido')
        path = self.base / 'jobs' / (jid + '.json')
        if not path.is_file():
            raise Error3D('Trabajo no encontrado')
        return path

    def activo(self):
        path = self.base / 'active.json'
        return leer(path).get('id') if path.is_file() else None

    def resumen(self):
        with self.mutex:
            projects = [leer(p) for p in (self.base / 'projects').glob('*/project.json')]
            jobs = [leer(p) for p in (self.base / 'jobs').glob('*.json')]
            return {'ok': True, 'active': self.activo(), 'engines': motores(),
                    'projects': [{k: p[k] for k in ('id', 'name', 'revision', 'updated')} for p in sorted(projects, key=lambda p: p['updated'], reverse=True)],
                    'jobs': sorted(jobs, key=lambda j: j['created'], reverse=True)[:30],
                    'catalog': {engine: {kind: {'inputs': spec[0], 'params': spec[1]} for kind, spec in ops.items()} for engine, ops in CATALOGO.items()}}

    def documento(self, pid):
        with self.mutex:
            path = self._project(pid)
            meta = leer(path / 'project.json')
            recipe = leer(path / 'versions' / meta['head'] / 'recipe.json')
            return {'ok': True, **meta, 'operations': recipe['operations'],
                    'can_undo': meta['cursor'] > 0, 'can_redo': meta['cursor'] < len(meta['history']) - 1,
                    'artifacts': self._artifacts(path / 'versions' / meta['head'])}

    def escena(self, pid, revision):
        with self.mutex:
            path = self._project(pid)
            meta = leer(path / 'project.json')
            if revision != meta['revision']:
                raise Error3D('El proyecto cambió. Actualiza el visor.')
            return {'ok': True, 'project': pid, 'revision': revision,
                    **leer(path / 'versions' / meta['head'] / 'scene.json')}

    @staticmethod
    def _artifacts(folder):
        return sorted(p.name for p in folder.iterdir() if p.suffix.lower() in ('.fcstd', '.blend', '.step', '.stl'))

    def archivo(self, pid, revision, name):
        with self.mutex:
            path = self._project(pid)
            meta = leer(path / 'project.json')
            if revision != meta['revision']:
                raise Error3D('La exportación pertenece a otra revisión')
            folder = path / 'versions' / meta['head']
            if name not in self._artifacts(folder):
                raise Error3D('Archivo no disponible')
            return folder / name

    def crear(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise Error3D('Escribe un nombre de proyecto de hasta 100 caracteres')
        pid, rev = uuid.uuid4().hex, uuid.uuid4().hex
        path = self.base / 'projects' / pid
        with self.mutex:
            guardar(path / 'versions' / rev / 'recipe.json', {'operations': []})
            guardar(path / 'versions' / rev / 'scene.json', {'units': 'mm', 'up': 'Z', 'objects': []})
            guardar(path / 'project.json', {'id': pid, 'name': name.strip(), 'revision': rev, 'head': rev,
                    'history': [rev], 'cursor': 0, 'updated': time.time(), 'notes': []})
            guardar(self.base / 'active.json', {'id': pid})
        return self.documento(pid)

    def _check(self, pid, revision):
        path = self._project(pid)
        meta = leer(path / 'project.json')
        if revision != meta['revision']:
            raise Error3D('El proyecto cambió desde que se preparó la operación. Consulta su revisión.')
        return path, meta

    def operar(self, request):
        if not isinstance(request, dict):
            raise Error3D('La solicitud debe ser un objeto JSON')
        op = request.get('op')
        if op == 'create':
            return self.crear(request.get('name'))
        if op == 'open':
            with self.mutex:
                doc = self.documento(request.get('project'))
                guardar(self.base / 'active.json', {'id': doc['id']})
                return doc
        if op == 'cancel':
            return self.cancelar(request.get('job'))
        with self.mutex:
            path, meta = self._check(request.get('project'), request.get('expected_revision'))
            if op in ('undo', 'redo', 'note'):
                lock = Bloqueo(self.base / 'trabajo.lock')
                try:
                    path, meta = self._check(request.get('project'), request.get('expected_revision'))
                    if op == 'note':
                        text = request.get('text')
                        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
                            raise Error3D('La indicación debe tener entre 1 y 2000 caracteres')
                        meta['notes'] = (meta['notes'] + [{'id': uuid.uuid4().hex, 'text': text.strip(),
                            'selection': str(request.get('selection') or '')[:64], 'revision': meta['revision'],
                            'created': time.time(), 'status': 'pending_agent'}])[-100:]
                    else:
                        cursor = meta['cursor'] + (-1 if op == 'undo' else 1)
                        if not 0 <= cursor < len(meta['history']):
                            raise Error3D('No hay otra revisión disponible')
                        meta.update(cursor=cursor, head=meta['history'][cursor], revision=uuid.uuid4().hex)
                    meta['updated'] = time.time()
                    guardar(path / 'project.json', meta)
                finally:
                    lock.close()
                return self.documento(meta['id'])
            previous = leer(path / 'versions' / meta['head'] / 'recipe.json')['operations']
            operations = copy.deepcopy(previous)
            if op == 'replace':
                # Complete declarative transaction; validated before any engine runs.
                operations = request.get('operations')
            elif op == 'append':
                added = request.get('operations')
                if not isinstance(added, list):
                    raise Error3D('Falta la lista de operaciones')
                operations += added
            elif op == 'update':
                targets = [item for item in operations if item['id'] == request.get('id')]
                if len(targets) != 1 or not isinstance(request.get('params', {}), dict):
                    raise Error3D('Objeto o parámetros inválidos')
                targets[0]['params'] = {**targets[0].get('params', {}), **request.get('params', {})}
                for key in ('label', 'color', 'visible'):
                    if key in request:
                        targets[0][key] = request[key]
            elif op == 'remove':
                if not any(item['id'] == request.get('id') for item in operations):
                    raise Error3D('Objeto no encontrado')
                operations = [item for item in operations if item['id'] != request['id']]
            elif op == 'demo':
                if operations:
                    raise Error3D('Abre un proyecto vacío para cargar el ejemplo')
                operations = ejemplo(request.get('example', 'mixto'))
            else:
                raise Error3D('Operación 3D desconocida')
            prepared = validar_operaciones(operations)
            operations = [{k: v for k, v in item.items() if k != 'display'} for item in prepared]
            engines = motores()
            for engine in {x['engine'] for x in prepared}:
                if not engines[engine]['available']:
                    raise Error3D(f'Motor {engine} no instalado o no configurado')
            lock = Bloqueo(self.base / 'trabajo.lock')
            jid = uuid.uuid4().hex
            job = {'id': jid, 'project': meta['id'], 'expected_revision': meta['revision'], 'created': time.time(),
                   'status': 'queued', 'message': 'Preparando geometría', 'error': None}
            self.cancels[jid] = threading.Event()
            try:
                guardar(self.base / 'jobs' / (jid + '.json'), job)
                thread = threading.Thread(target=self._run, args=(job, operations, prepared, engines, lock), daemon=True)
                thread.start()
            except Exception:
                self.cancels.pop(jid, None)
                lock.close()
                raise
            return {'ok': True, 'job': job}

    def cancelar(self, jid):
        with self.mutex:
            path = self._job(jid)
            job = leer(path)
            if job['status'] not in ('queued', 'running', 'cancelling'):
                return {'ok': True, 'job': job}
            if jid not in self.cancels:
                raise Error3D('La operación pertenece a otra instancia del servicio')
            self.cancels[jid].set()
            job.update(status='cancelling', message='Deteniendo el motor')
            guardar(path, job)
            return {'ok': True, 'job': job}

    def trabajo(self, jid):
        with self.mutex:
            return {'ok': True, **leer(self._job(jid))}

    def _run(self, job, operations, prepared, engines, lock):
        jid, pid = job['id'], job['project']
        cancel = self.cancels[jid]
        folder = self._project(pid) / 'versions' / jid
        folder.mkdir(parents=True, exist_ok=True)

        def status(**fields):
            with self.mutex:
                job.update(fields)
                guardar(self.base / 'jobs' / (jid + '.json'), job)

        try:
            guardar(folder / 'request.json', {'operations': prepared})
            objects = []
            versions = {}
            for engine in sorted({item['engine'] for item in prepared}):
                if cancel.is_set():
                    raise InterruptedError('Operación cancelada')
                status(status='running', message=f'{engine}: construyendo geometría')
                exe = engines[engine]['executable']
                if engine == 'freecad':
                    cmd = [exe, str(WORKERS / 'worker_freecad.py'), str(folder / 'request.json'), str(folder)]
                else:
                    cmd = [exe, '--background', '--factory-startup', '--python-exit-code', '1', '--python',
                           str(WORKERS / 'worker_bmesh.py'), '--', str(folder / 'request.json'), str(folder)]
                log = folder / (engine + '.log')
                with log.open('w', encoding='utf-8') as handle:
                    env = dict(os.environ, PYTHONIOENCODING='utf-8')
                    # FreeCAD and Blender use their bundled Python; host PYTHONPATH must not leak in.
                    env.pop('PYTHONHOME', None)
                    env.pop('PYTHONPATH', None)
                    process = subprocess.Popen(cmd, cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
                                               env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    deadline = time.monotonic() + 180
                    try:
                        while process.poll() is None:
                            if cancel.wait(0.15):
                                raise InterruptedError('Operación cancelada; se conserva la revisión anterior')
                            if time.monotonic() > deadline:
                                raise Error3D('El motor excedió los 180 segundos; se conserva la revisión anterior')
                        if process.returncode != 0:
                            tail = log.read_text(encoding='utf-8', errors='replace')[-1800:]
                            raise Error3D(f'{engine} no pudo completar la operación: {tail}')
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=10)
                result = leer(folder / (engine + '-scene.json'))
                objects += result['objects']
                versions[engine] = result['version']
            if sum(len(o['indices']) // 3 for o in objects) > MAX_TRIANGULOS:
                raise Error3D('El conjunto supera el presupuesto de triángulos del visor')
            scene = {'units': 'mm', 'up': 'Z', 'engines': versions, 'objects': objects}
            guardar(folder / 'scene.json', scene)
            guardar(folder / 'recipe.json', {'operations': operations})
            with self.mutex:
                if cancel.is_set():
                    raise InterruptedError('Operación cancelada antes de publicar')
                path, meta = self._check(pid, job['expected_revision'])
                history = meta['history'][:meta['cursor'] + 1] + [jid]
                meta.update(head=jid, revision=jid, history=history, cursor=len(history)-1, updated=time.time())
                guardar(path / 'project.json', meta)
                status(status='complete', message='Geometría guardada y publicada', revision=jid, finished=time.time())
        except InterruptedError as exc:
            status(status='cancelled', error=str(exc), message='Cancelado', finished=time.time())
        except Exception as exc:
            status(status='failed', error=str(exc), message='No se modificó el documento', finished=time.time())
        finally:
            lock.close()
            with self.mutex:
                self.cancels.pop(jid, None)


SERVICIO = Servicio3D()
