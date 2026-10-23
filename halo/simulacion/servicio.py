"""Trabajos persistentes y cancelables. Nunca ejecuta ngspice en el servidor web."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from ..kicad.biblioteca import KICAD_DIR
from ..kicad.proyecto import atomico, bloquear, comprobar, revision
from .ejemplos import ejemplo
from .medidas import resumir
from .preparar import ErrorSimulacion, cargar_modelos, compilar, exportar, huella, plantilla, validar_config

REPO = Path(__file__).resolve().parents[2]
CARPETA = REPO / 'build' / 'halo' / 'simulaciones'
CONFIG = '.halo/simulacion/config.json'
ACTIVOS = ('ejecutando', 'cancelando')


AL_CAMBIAR = None   # lo fija el servidor: avisa a la interfaz cada vez que cambia una simulación


def guardar_json(ruta, datos):
    atomico(ruta, json.dumps(datos, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))
    if AL_CAMBIAR:
        AL_CAMBIAR()


def dll_ngspice():
    return Path(os.environ.get('HALO_NGSPICE_DLL', str(KICAD_DIR / 'bin' / 'ngspice.dll'))).resolve()


def proyecto(actual, peticion):
    sch = actual.get('archivos', {}).get('esquematico')
    if not sch and str(actual.get('archivo', '')).endswith('.kicad_sch'):
        sch = actual['archivo']
    if not sch:
        raise ErrorSimulacion('Abre un proyecto con esquemático antes de simularlo')
    sch = Path(sch).resolve()
    if str(sch.parent) != peticion.get('proyecto_id'):
        raise ErrorSimulacion('El proyecto activo cambió')
    comprobar(sch.parent, peticion.get('expected_revision'))
    return sch


def vigencia(meta, cache=None):
    if not meta.get('proyecto_id'):
        return 'ejemplo'
    carpeta = Path(meta['proyecto_id']).resolve()
    # Cache limited to ONE history request. Never reuse a fingerprint across polls.
    cache = {} if cache is None else cache
    try:
        clave = ('revision', str(carpeta))
        if clave not in cache:
            cache[clave] = revision(carpeta)
        if cache[clave] != meta['revision']:
            return 'obsoleto'
        for ruta, hash_ in meta['dependencias'].items():
            p = (carpeta / ruta).resolve()
            if not p.is_relative_to(carpeta):
                return 'obsoleto'
            clave = ('archivo', str(p))
            if clave not in cache:
                cache[clave] = huella(p.read_bytes()) if p.is_file() else None
            if cache[clave] != hash_:
                return 'obsoleto'
    except OSError:
        return 'obsoleto'
    return 'vigente'


class Servicio:
    def __init__(self, carpeta=CARPETA):
        self.carpeta = Path(carpeta)
        self.lock = threading.RLock()
        self.procesos = {}
        self.cancelaciones = set()

    def ruta(self, id_):
        if not isinstance(id_, str) or len(id_) != 32 or any(c not in '0123456789abcdef' for c in id_):
            raise ErrorSimulacion('Identificador de ejecución inválido')
        ruta = self.carpeta / id_
        if not (ruta / 'meta.json').is_file():
            raise ErrorSimulacion('Ejecución inexistente')
        return ruta

    def leer(self, id_, completo=True):
        with self.lock:
            ruta = self.ruta(id_)
            meta = json.loads((ruta / 'meta.json').read_text(encoding='utf-8'))
            meta['vigencia'] = vigencia(meta)
            if completo and meta['estado'] == 'completado' and (ruta / 'resultado.json').is_file():
                meta['resultado'] = json.loads((ruta / 'resultado.json').read_text(encoding='utf-8'))
            return meta

    def listar(self, proyecto_id=None):
        """Las ejecuciones del proyecto, de la más nueva a la más vieja; los ejemplos sueltos van al final,
        para que no se mezclen con el trabajo real del proyecto."""
        propias, ejemplos, cache = [], [], {}
        for p in sorted(self.carpeta.glob('*/meta.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                m = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if m.get('proyecto_id') == proyecto_id and proyecto_id is not None:
                destino = propias
            elif m.get('proyecto_id') is None:
                destino = ejemplos
            else:
                continue
            m['vigencia'] = vigencia(m, cache)
            destino.append(m)
            if len(propias) + len(ejemplos) >= 30:
                break
        return propias + ejemplos

    def recuperar(self):
        """Only the server owner calls this once, on startup; not short-lived CLI clients."""
        for p in self.carpeta.glob('*/meta.json'):
            m = json.loads(p.read_text(encoding='utf-8'))
            if m['estado'] in ACTIVOS:
                m.update(estado='interrumpido', error='El servidor se cerró antes de recibir el resultado')
                guardar_json(p, m)

    def configurar(self, actual, peticion):
        sch = proyecto(actual, peticion)
        config = peticion.get('config')
        validar_config(config)
        with bloquear(sch.parent):
            comprobar(sch.parent, peticion['expected_revision'])
            guardar_json(sch.parent / CONFIG, config)
        return {'ok': True}

    def configuracion(self, actual):
        sch = actual.get('archivos', {}).get('esquematico')
        if not sch:
            return None
        p = Path(sch).parent / CONFIG
        return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None

    def inventario(self, actual, peticion):
        sch = proyecto(actual, peticion)
        import tempfile
        with bloquear(sch.parent), tempfile.TemporaryDirectory(prefix='halo-netlist-') as tmp:
            comprobar(sch.parent, peticion['expected_revision'])
            componentes = exportar(sch, Path(tmp) / 'circuito.xml')
            comprobar(sch.parent, peticion['expected_revision'])
        return {'ok': True, 'componentes': componentes, 'config': plantilla(componentes),
                'redes': sorted({n for c in componentes.values() for n in c['pines'].values()})}

    def preparar(self, actual, peticion):
        id_ = uuid.uuid4().hex
        self.carpeta.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.preparando-', dir=self.carpeta) as tmp:
            destino = Path(tmp).resolve()
            assert destino.parent == self.carpeta.resolve()
            self._preparar(actual, peticion, destino, id_)
            destino.rename(self.carpeta / id_)
        return {'ok': True, **self.leer(id_)}

    def _preparar(self, actual, peticion, destino, id_):
        dll = dll_ngspice()
        if not dll.is_file():
            raise ErrorSimulacion('No se encontró ngspice. Configura HALO_NGSPICE_DLL en el servidor.')
        dependencias, proyecto_id, rev = {}, None, None
        if peticion.get('ejemplo'):
            config, componentes = ejemplo(peticion['ejemplo'])
            textos = config.get('modelos_inline', [])
        else:
            sch = proyecto(actual, peticion)
            proyecto_id = str(sch.parent)
            with bloquear(sch.parent):
                rev = comprobar(sch.parent, peticion['expected_revision'])
                config_path = sch.parent / CONFIG
                config_bytes = config_path.read_bytes() if config_path.is_file() else None
                config = peticion.get('config')
                if config is None:
                    if config_bytes is None:
                        raise ErrorSimulacion('Falta configurar los modelos, fuentes y sondas del proyecto')
                    config = json.loads(config_bytes)
                validar_config(config)
                dependencias[CONFIG] = huella(config_bytes) if config_bytes is not None else None
                componentes = exportar(sch, destino / 'netlist.xml')
                # Prevent reusing stale numeric substitutions after a schematic value edit.
                for ref, c in componentes.items():
                    m = config.get('componentes', {}).get(ref, {})
                    if m.get('valor_esquematico') != c['valor']:
                        raise ErrorSimulacion(f'{ref}: confirma valor_esquematico={c["valor"]!r} en la asignación')
                textos, deps = cargar_modelos(sch.parent, config)
                dependencias.update(deps)
                comprobar(sch.parent, rev)
        preparado = compilar(config, componentes, textos)
        preparado['dll'] = str(dll)
        motor_hash = huella(dll.read_bytes())
        firma = huella({'revision': rev, 'config': config, 'deck': preparado['deck'], 'motor': motor_hash})
        meta = {'id': id_, 'estado': 'preparado', 'nombre': config.get('nombre', 'Simulación'),
                'creado': time.time(), 'proyecto_id': proyecto_id, 'revision': rev,
                'dependencias': dependencias, 'firma': firma, 'motor_sha256': motor_hash,
                'entrada_sha256': huella(preparado),
                'analisis': config['analisis'], 'exclusiones': preparado['exclusiones'],
                'limitaciones': preparado['limitaciones'], 'ejemplo': peticion.get('ejemplo')}
        guardar_json(destino / 'entrada.json', preparado)
        guardar_json(destino / 'config.json', config)
        guardar_json(destino / 'componentes.json', componentes)
        atomico(destino / 'circuito.cir', preparado['deck'].encode('utf-8'))
        guardar_json(destino / 'meta.json', meta)

    def ejecutar(self, id_, timeout=60):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ErrorSimulacion('El tiempo límite debe estar entre 1 y 120 segundos')
        with self.lock:
            meta = self.leer(id_, False)
            if meta['estado'] != 'preparado':
                raise ErrorSimulacion('Esta ejecución ya se utilizó; prepara otra para repetir')
            if meta['vigencia'] == 'obsoleto':
                raise ErrorSimulacion('Entradas obsoletas; prepara una nueva simulación')
            if len(self.procesos) >= 2:
                raise ErrorSimulacion('Ya hay dos simulaciones en curso')
            ruta = self.ruta(id_)
            preparado = json.loads((ruta / 'entrada.json').read_text(encoding='utf-8'))
            if huella(preparado) != meta['entrada_sha256']:
                raise ErrorSimulacion('La instantánea fue modificada; prepara otra simulación')
            if huella(Path(preparado['dll']).read_bytes()) != meta['motor_sha256']:
                raise ErrorSimulacion('El motor cambió; prepara otra simulación')
            worker = Path(__file__).with_name('worker.py')
            with (ruta / 'worker.log').open('wb') as log:
                proceso = subprocess.Popen([sys.executable, str(worker), str(ruta / 'entrada.json'), str(ruta / 'crudo.json')],
                                           cwd=str(ruta), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                self.procesos[id_] = proceso
                meta.update(estado='ejecutando', inicio=time.time(), timeout=timeout)
                guardar_json(ruta / 'meta.json', meta)
                threading.Thread(target=self._esperar, args=(id_, proceso, timeout), daemon=True).start()
            except Exception:
                if proceso.poll() is None:
                    proceso.kill()
                proceso.wait()
                self.procesos.pop(id_, None)
                raise
            return {'ok': True, **meta}

    def _esperar(self, id_, proceso, timeout):
        ruta = self.ruta(id_)
        estado, error, resultado = 'completado', None, None
        try:
            proceso.wait(timeout=timeout)
            if proceso.returncode:
                raise RuntimeError(f'El motor terminó con código {proceso.returncode}')
            datos = json.loads((ruta / 'crudo.json').read_text(encoding='utf-8'))
            if not datos.get('ok'):
                raise RuntimeError(datos.get('error', 'El motor no entregó resultados'))
            preparado = json.loads((ruta / 'entrada.json').read_text(encoding='utf-8'))
            if huella(preparado) != self.leer(id_, False)['entrada_sha256']:
                raise RuntimeError('La instantánea cambió durante la ejecución')
            resultado = resumir(datos, preparado)
        except subprocess.TimeoutExpired:
            proceso.kill()
            proceso.wait()
            estado, error = 'timeout', 'Se agotó el tiempo límite. El circuito no está verificado.'
        except Exception as exc:
            estado, error = 'error', str(exc)
        with self.lock:
            if id_ in self.cancelaciones:
                self.cancelaciones.remove(id_)
                estado, error, resultado = 'cancelado', None, None
            meta = self.leer(id_, False)
            meta.update(estado=estado, error=error, fin=time.time())
            if resultado is not None:
                guardar_json(ruta / 'resultado.json', resultado)
                meta['criterios'] = resultado['criterios']
            guardar_json(ruta / 'meta.json', meta)
            self.procesos.pop(id_, None)

    def cancelar(self, id_):
        with self.lock:
            meta = self.leer(id_, False)
            if id_ in self.procesos:
                self.cancelaciones.add(id_)
                if self.procesos[id_].poll() is None:
                    self.procesos[id_].kill()
                meta['estado'] = 'cancelando'
            elif meta['estado'] == 'preparado':
                meta['estado'] = 'cancelado'
            guardar_json(self.ruta(id_) / 'meta.json', meta)
            return {'ok': True, **meta}

    def comparar(self, id_, base):
        a, b = self.leer(base), self.leer(id_)
        if any(m['estado'] != 'completado' for m in (a, b)):
            raise ErrorSimulacion('Sólo se comparan simulaciones completadas')
        if a['proyecto_id'] != b['proyecto_id'] or a['analisis']['tipo'] != b['analisis']['tipo']:
            raise ErrorSimulacion('La comparación requiere el mismo proyecto y tipo de análisis')
        anteriores = {m['id']: m for m in a['resultado']['medidas']}
        sondas_a = {s['id']: s for s in a['resultado']['series']}
        sondas_b = {s['id']: s for s in b['resultado']['series']}
        cambios = []
        for m in b['resultado']['medidas']:
            p = anteriores.get(m['id'])
            # Bounds may change, but the measured quantity must not.
            claves = ('sonda', 'metrica', 'en', 'umbral', 'desde', 'hasta')
            sa, sb = sondas_a.get(m['sonda'], {}), sondas_b[m['sonda']]
            if p and all(m.get(k) == p.get(k) for k in claves) and all(sa.get(k) == sb.get(k) for k in ('red', 'corriente', 'unidad', 'representacion')):
                cambios.append({'id': m['id'], 'antes': p['valor'], 'despues': m['valor'],
                                'delta': m['valor'] - p['valor'] if m['valor'] is not None and p['valor'] is not None else None})
        return {'ok': True, 'base': base, 'id': id_, 'vigencia_base': a['vigencia'],
                'vigencia': b['vigencia'], 'cambios': cambios,
                'nota': 'Diferencias numéricas; no implican una mejora automática ni aprobación del diseño.'}


SERVICIO = Servicio()
