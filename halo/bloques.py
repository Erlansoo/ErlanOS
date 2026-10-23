"""Dossiers de bloques: documentos locales con evidencia verificable, sin ejecutar código."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from .kicad.proyecto import revision


class ErrorBloque(ValueError):
    pass


def leer_documento(ruta: Path) -> dict:
    if not ruta.exists():
        return {'version': 1, 'bloques': []}
    datos = json.loads(ruta.read_text(encoding='utf-8'))
    if not isinstance(datos, dict) or datos.get('version') != 1 or not isinstance(datos.get('bloques'), list):
        raise ErrorBloque('Formato de fichas de bloques no reconocido')
    uuids = set()
    for f in datos['bloques']:
        if not isinstance(f, dict):
            raise ErrorBloque('Cada ficha debe ser un objeto')
        uid = f.get('rectangulo_uuid')
        if not isinstance(uid, str) or not uid or uid in uuids:
            raise ErrorBloque('Cada ficha debe identificar un rectángulo único')
        uuids.add(uid)
        for clave in ('referencias', 'decisiones', 'pendientes', 'interfaces'):
            lista = f.get(clave, [])
            if not isinstance(lista, list) or not all(isinstance(t, str) for t in lista):
                raise ErrorBloque(f'{clave} debe contener textos')
        for clave in ('pruebas', 'fuentes'):
            lista = f.get(clave, [])
            if not isinstance(lista, list) or not all(isinstance(t, dict) and all(isinstance(v, str) for v in t.values()) for t in lista):
                raise ErrorBloque(f'{clave} debe contener objetos con campos de texto')
    return datos


def incorporar(modelo: dict) -> dict:
    """Marcos de documentación del visor; nunca modifican el esquemático de KiCad."""
    ruta = Path(modelo['archivo']).parent / '.halo' / 'bloques.json'
    if not ruta.is_file():
        return modelo
    try:
        datos = leer_documento(ruta)
    except (ValueError, OSError) as exc:
        modelo['error_bloques'] = str(exc)
        return modelo
    existentes = {b['uuid'] for b in modelo.get('bloques', [])}
    for b in datos.get('bloques', []):
        uid = b.get('rectangulo_uuid', '')
        puntos = [b.get('a'), b.get('b')]
        if not uid.startswith('halo-') or uid in existentes:
            continue
        if not all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in p) for p in puntos):
            continue
        modelo['bloques'].append({'uuid': uid, 'a': puntos[0], 'b': puntos[1],
                                 'titulo': b.get('titulo', ''), 'virtual': True, 'discontinuo': True})
        if 'limites' in modelo:
            limites = modelo['limites']
            modelo['limites'] = [min(limites[0], *(p[0] for p in puntos)),
                                 min(limites[1], *(p[1] - 3 for p in puntos)),
                                 max(limites[2], *(p[0] for p in puntos)),
                                 max(limites[3], *(p[1] for p in puntos))]
        existentes.add(uid)
        # El título ya es un texto real en KiCad: el visor lo integra en el marco clicable.
        x, y = min(p[0] for p in puntos), min(p[1] for p in puntos) - 1.27
        modelo['textos'] = [t for t in modelo.get('textos', [])
                            if not (t.get('s') == b.get('titulo') and abs(t['p'][0] - x) < .02 and abs(t['p'][1] - y) < .02)]
    return modelo


def archivo_local(carpeta: Path, ruta: str) -> Path:
    raiz = carpeta.resolve()
    archivo = (raiz / ruta).resolve()
    if not archivo.is_relative_to(raiz) or not archivo.is_file():
        raise ErrorBloque('La evidencia no existe dentro de este proyecto')
    return archivo


def consultar(actual: dict, modelo: dict, uuid: str | None = None, leer_simulacion=None) -> dict:
    origen = (actual.get('archivos') or {}).get('esquematico')
    if not origen or not modelo or Path(modelo.get('archivo', '')).resolve() != Path(origen).resolve():
        raise ErrorBloque('No hay un esquemático activo listo para consultar')
    carpeta = Path(origen).resolve().parent
    rev = revision(carpeta)
    if actual.get('revision') != rev:
        raise ErrorBloque('El proyecto está cambiando; vuelve a abrir la ficha')
    ruta = carpeta / '.halo' / 'bloques.json'
    documento = leer_documento(ruta)
    fichas = {f['rectangulo_uuid']: f for f in documento['bloques']}
    bloques = modelo.get('bloques', [])
    base = {'ok': True, 'proyecto_id': str(carpeta), 'revision': rev}
    if uuid is None:
        return {**base, 'bloques': [{'uuid': b['uuid'], 'titulo': b.get('titulo') or 'Bloque',
                                    'documentado': b['uuid'] in fichas} for b in bloques]}
    bloque = next((b for b in bloques if b['uuid'] == uuid), None)
    if bloque is None:
        raise ErrorBloque('El bloque ya no pertenece al esquemático activo')
    ficha = dict(fichas.get(uuid) or {})
    refs = set(ficha.get('referencias', []))
    componentes = {s['ref']: {'ref': s['ref'], 'valor': s.get('valor'), 'lib_id': s.get('lib_id')}
                   for s in modelo.get('simbolos', []) if s.get('ref') in refs and not s.get('power')}
    presentes = {s.get('ref') for s in modelo.get('simbolos', [])}
    pruebas = []
    for prueba in ficha.get('pruebas', []):
        p = dict(prueba)
        p['verificacion'] = 'pendiente'
        if p.get('ruta') and p.get('sha256'):
            try:
                archivo = archivo_local(carpeta, p['ruta'])
                coincide = hashlib.sha256(archivo.read_bytes()).hexdigest() == p['sha256']
                p['verificacion'] = ('vigente' if p.get('revision') == rev else 'desactualizada') if coincide else 'evidencia_modificada'
            except (OSError, ErrorBloque):
                p['verificacion'] = 'no_disponible'
        if p.get('simulacion_id') and leer_simulacion:
            try:
                sim = leer_simulacion(p['simulacion_id'])
                pertenece = sim.get('proyecto_id') == str(carpeta) and not sim.get('ejemplo')
                p['verificacion'] = 'no_corresponde'
                if pertenece:
                    p['verificacion'] = 'vigente' if sim.get('vigencia') == 'vigente' and sim.get('estado') == 'completado' else 'no_validada'
                    p['resultado'] = (sim.get('resultado') or {}).get('criterios')
            except (ValueError, OSError):
                p['verificacion'] = 'no_disponible'
        pruebas.append(p)
    fuentes = []
    for fuente in ficha.get('fuentes', []):
        f = dict(fuente)
        url = f.get('url', '')
        if urlparse(url).scheme not in ('https', 'http'):
            f.pop('url', None)
        fuentes.append(f)
    ficha.update(rectangulo_uuid=uuid, titulo=bloque.get('titulo') or ficha.get('titulo') or 'Bloque',
                 componentes=list(componentes.values()), referencias_ausentes=sorted(refs - presentes),
                 pruebas=pruebas, fuentes=fuentes, documentado=uuid in fichas,
                 vigencia='vigente' if documento.get('revision') == rev else 'por_revisar')
    if revision(carpeta) != rev:
        raise ErrorBloque('El proyecto cambió durante la consulta')
    return {**base, 'ficha': ficha}
