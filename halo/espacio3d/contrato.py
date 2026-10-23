"""El mismo contrato limita la API, los agentes y los procesos de geometría."""
from __future__ import annotations

import copy
import math
import re


class Error3D(ValueError):
    pass


ID = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
MAX_OPERACIONES = 100
MAX_TRIANGULOS = 150000
CATALOGO = {
    'freecad': {
        'box': (0, {'width': 40, 'depth': 30, 'height': 10}),
        'cylinder': (0, {'radius': 5, 'height': 20}),
        'sphere': (0, {'radius': 10}),
        'cone': (0, {'radius1': 10, 'radius2': 0, 'height': 20}),
        'sketch_rectangle': (0, {'width': 40, 'depth': 30}),
        'sketch_circle': (0, {'radius': 10}),
        'extrude': (1, {'length': 10}),
        'cut': (2, {}), 'fuse': (2, {}), 'common': (2, {}),
        'fillet': (1, {'radius': 1, 'edges': []}),
        'chamfer': (1, {'distance': 1, 'edges': []}),
        'transform': (1, {'translation': [0, 0, 0], 'rotation': [0, 0, 0]}),
    },
    'bmesh': {
        'box': (0, {'width': 20, 'depth': 20, 'height': 20}),
        'sphere': (0, {'radius': 15, 'subdivisions': 2}),
        'cone': (0, {'radius1': 15, 'radius2': 8, 'height': 25, 'segments': 32}),
        'bevel': (1, {'radius': 1, 'segments': 3}),
        'subdivide': (1, {'cuts': 1}),
        'smooth': (1, {'factor': 0.5, 'iterations': 1}),
        'transform': (1, {'translation': [0, 0, 0], 'rotation': [0, 0, 0], 'scale': [1, 1, 1]}),
    },
}


def numero(value, nombre, minimo=-100000, maximo=100000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Error3D(f'{nombre}: se requiere un número finito')
    if not minimo <= value <= maximo:
        raise Error3D(f'{nombre}: fuera de rango [{minimo}, {maximo}]')
    return float(value)


def validar_operaciones(operations):
    if not isinstance(operations, list) or len(operations) > MAX_OPERACIONES:
        raise Error3D(f'Se admiten hasta {MAX_OPERACIONES} operaciones por documento')
    result, prev = [], {}
    for raw in operations:
        if not isinstance(raw, dict) or set(raw) - {'id', 'engine', 'kind', 'label', 'params', 'inputs', 'visible', 'color'}:
            raise Error3D('Operación inválida o campos desconocidos')
        oid, engine, kind = raw.get('id'), raw.get('engine'), raw.get('kind')
        if not isinstance(oid, str) or not ID.fullmatch(oid) or oid in prev:
            raise Error3D('Cada operación necesita un identificador único y estable')
        if engine not in CATALOGO or kind not in CATALOGO[engine]:
            raise Error3D(f'Operación no admitida: {engine}/{kind}')
        arity, defaults = CATALOGO[engine][kind]
        inputs = raw.get('inputs', [])
        if not isinstance(inputs, list) or len(inputs) != arity:
            raise Error3D(f'{oid}: necesita {arity} entradas')
        for parent in inputs:
            if not isinstance(parent, str) or parent not in prev or prev[parent]['engine'] != engine:
                raise Error3D(f'{oid}: entrada ausente, posterior o de otro motor')
        if len(set(inputs)) != len(inputs):
            raise Error3D(f'{oid}: las entradas deben ser diferentes')
        params = raw.get('params', {})
        allowed = set(defaults) | ({'position'} if not arity else set())
        if not isinstance(params, dict) or set(params) - allowed:
            raise Error3D(f'{oid}: parámetros desconocidos')
        params = {**copy.deepcopy(defaults), **copy.deepcopy(params)}
        for name, value in params.items():
            if name in ('position', 'translation', 'rotation', 'scale'):
                if not isinstance(value, list) or len(value) != 3:
                    raise Error3D(f'{oid}/{name}: se requieren tres números')
                low = 0.001 if name == 'scale' else -100000
                high = 1000 if name == 'scale' else 100000
                params[name] = [numero(v, name, low, high) for v in value]
            elif name == 'edges':
                if not isinstance(value, list) or len(value) > 500 or any(type(v) is not int or v < 1 for v in value):
                    raise Error3D('edges: índices positivos de aristas, o [] para todas')
            elif name in ('segments', 'subdivisions', 'cuts', 'iterations'):
                ranges = {'segments': (1, 64), 'subdivisions': (1, 4), 'cuts': (1, 3), 'iterations': (1, 10)}
                lo, hi = ranges[name]
                if type(value) is not int or not lo <= value <= hi or (kind == 'cone' and name == 'segments' and value < 3):
                    raise Error3D(f'{name}: número entero fuera de rango')
            elif name == 'factor':
                numero(value, name, 0, 1)
            else:
                numero(value, name, 0 if name == 'radius2' else 0.001, 10000)
        label = raw.get('label', oid)
        if not isinstance(label, str) or not 1 <= len(label) <= 120:
            raise Error3D('El nombre de la pieza debe tener entre 1 y 120 caracteres')
        color = raw.get('color', '#cfaa78' if engine == 'freecad' else '#76a9ad')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise Error3D('Color inválido')
        if 'visible' in raw and type(raw['visible']) is not bool:
            raise Error3D('visible debe ser booleano')
        item = dict(raw, label=label, params=params, inputs=inputs, color=color)
        prev[oid] = item
        result.append(item)
    consumed = {parent for item in result for parent in item['inputs']}
    for item in result:
        item['display'] = item.get('visible', item['id'] not in consumed)
    return result


def ejemplo(nombre):
    if nombre == 'cad':
        return [
            {'id': 'perfil', 'engine': 'freecad', 'kind': 'sketch_rectangle', 'label': 'Perfil acotado', 'params': {'width': 64, 'depth': 40}},
            {'id': 'base', 'engine': 'freecad', 'kind': 'extrude', 'label': 'Base · 6 mm', 'inputs': ['perfil'], 'params': {'length': 6}},
            {'id': 'broca', 'engine': 'freecad', 'kind': 'cylinder', 'label': 'Taladro · diámetro 10', 'params': {'radius': 5, 'height': 10, 'position': [15, 20, -2]}},
            {'id': 'soporte', 'engine': 'freecad', 'kind': 'cut', 'label': 'Soporte con taladro', 'inputs': ['base', 'broca']},
        ]
    if nombre == 'malla':
        return [
            {'id': 'forma', 'engine': 'bmesh', 'kind': 'cone', 'label': 'Forma decorativa', 'params': {'radius1': 13, 'radius2': 8, 'height': 28, 'segments': 12, 'position': [93, 20, 14]}},
            {'id': 'escultura', 'engine': 'bmesh', 'kind': 'bevel', 'label': 'Estudio facetado', 'inputs': ['forma'], 'params': {'radius': 1, 'segments': 3}},
        ]
    if nombre == 'mixto':
        return ejemplo('cad') + ejemplo('malla')
    raise Error3D('Ejemplo desconocido')
