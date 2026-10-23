"""Preparación de fabricación con KiCad: nunca confunde exportación con aprobación.

python -m halo.fabricacion --placa ruta.kicad_pcb
No edita el diseño, no rellena zonas, no sube archivos ni compra placas.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid
import zipfile

from .kicad import sexpr as sx
from .kicad.oraculo import CLI
from .kicad.proyecto import archivos, atomico, bloquear, comprobar, revision
from .kicad.verificador import informe_json

REPO = Path(__file__).resolve().parents[1]
SALIDA = REPO / 'build/halo/fabricacion'
GUIA = 'https://jlcpcb.com/help/article/how-to-generate-gerber-and-drill-files-in-kicad-7'
_lock = threading.Lock()


class ErrorFabricacion(ValueError):
    pass


def sha(datos):
    return hashlib.sha256(datos).hexdigest()


def inventario(placa):
    """Lectura de metadatos no disponibles en el estado del visor, sin reserializar."""
    raiz = sx.loads(Path(placa).read_text(encoding='utf-8'))
    if not raiz or raiz[0] != 'kicad_pcb':
        raise ErrorFabricacion('No es una placa nativa de KiCad')
    capas = [str(c[1]) for c in (sx.find(raiz, 'layers') or [])[1:] if str(c[1]).endswith('.Cu')]
    capas.sort(key=lambda n: 0 if n == 'F.Cu' else 999 if n == 'B.Cu' else int(re.search(r'\d+', n)[0]))
    huellas = sx.find_all(raiz, 'footprint')
    pth = npth = 0
    for h in huellas:
        for p in sx.find_all(h, 'pad'):
            if sx.find(p, 'drill') is not None:
                if str(p[2]) == 'np_thru_hole':
                    npth += 1
                else:
                    pth += 1
    vias = sx.find_all(raiz, 'via')
    especiales = [v for v in vias if list(map(str, (sx.find(v, 'layers') or [])[1:])) != ['F.Cu', 'B.Cu']
                  or (len(v) > 1 and not isinstance(v[1], list))]
    bordes = sum(1 for e in raiz[1:] if isinstance(e, list) and sx.val(e, 'layer', '') == 'Edge.Cuts')
    return {'capas_cobre': capas, 'huellas': len(huellas), 'elementos_contorno': bordes,
            'taladros_pth': pth + len(vias), 'taladros_npth': npth,
            'vias_especiales': len(especiales), 'espesor_mm': sx.val(sx.find(raiz, 'general') or [], 'thickness', None)}


def resumen_drc(datos):
    for campo in ('violations', 'unconnected_items', 'schematic_parity'):
        if not isinstance(datos.get(campo), list):
            raise ErrorFabricacion('DRC incompleto: falta ' + campo)
        if any(not isinstance(v, dict) or v.get('severity') not in ('error', 'warning', 'ignore') for v in datos[campo]):
            raise ErrorFabricacion('DRC con severidades desconocidas')
    todos = sum((datos[k] for k in ('violations', 'unconnected_items', 'schematic_parity')), [])
    return {'errores': sum(v['severity'] == 'error' for v in todos),
            'avisos': sum(v['severity'] == 'warning' for v in todos),
            'excluidos': sum(v['severity'] == 'ignore' for v in todos),
            'sin_conectar': len(datos['unconnected_items']), 'paridad': len(datos['schematic_parity'])}


def funciones_esperadas(inv):
    capas = inv['capas_cobre']
    funciones = {f'Copper,L{i + 1},{"Top" if c == "F.Cu" else "Bot" if c == "B.Cu" else "Inr"}'
                 for i, c in enumerate(capas)}
    funciones.update({'Soldermask,Top', 'Soldermask,Bot', 'Legend,Top', 'Legend,Bot',
                      'SolderPaste,Top', 'SolderPaste,Bot', 'Profile,NP'})
    return {normalizar_funcion(f) for f in funciones}


def normalizar_funcion(funcion):
    # KiCad's .gbrjob and X2 headers use these different official spellings.
    return funcion.replace('SolderPaste,', 'Paste,').replace('SolderMask,', 'Soldermask,').replace('Profile,NP', 'Profile')


def validar_salidas(carpeta, inv):
    """Acepta capas por atributos X2 y contenido, no sólo por extensión/nombre."""
    carpeta = Path(carpeta).resolve()
    manifest, problemas = [], []
    jobs = list(carpeta.glob('*.gbrjob'))
    if len(jobs) != 1:
        return [], ['Falta un único manifiesto Gerber de KiCad (.gbrjob)']
    job = json.loads(jobs[0].read_text(encoding='utf-8'))
    vistos = set()
    for entrada in job.get('FilesAttributes', []):
        nombre, funcion = entrada.get('Path'), entrada.get('FileFunction')
        if not isinstance(nombre, str) or not isinstance(funcion, str):
            problemas.append('Entrada Gerber incompleta')
            continue
        p = (carpeta / nombre).resolve()
        if p.parent != carpeta or not p.is_file() or p.is_symlink():
            problemas.append('Archivo Gerber ausente o ruta inválida: ' + nombre)
            continue
        datos = p.read_bytes()
        texto = datos.decode('ascii', 'replace')
        atributo = re.search(r'%TF\.FileFunction,([^*]+)\*%', texto)
        funcion = normalizar_funcion(funcion)
        if (not datos or not atributo or normalizar_funcion(atributo[1]) != funcion
                or not texto.rstrip().endswith('M02*') or '%MOMM*%' not in texto
                or '%TF.SameCoordinates,Original*%' not in texto):
            problemas.append('Gerber vacío, truncado o con capa incorrecta: ' + nombre)
            continue
        if funcion in vistos:
            problemas.append('Capa duplicada: ' + funcion)
        vistos.add(funcion)
        manifest.append({'archivo': nombre, 'tipo': 'gerber', 'funcion': funcion, 'bytes': len(datos), 'sha256': sha(datos)})
    for funcion in sorted(funciones_esperadas(inv) - vistos):
        problemas.append('Falta capa: ' + funcion)
    if vistos - funciones_esperadas(inv):
        problemas.append('Hay capas adicionales sin explicar en el paquete')
    agujeros = set()
    for p in sorted(carpeta.glob('*.drl')):
        datos = p.read_bytes()
        texto = datos.decode('ascii', 'replace')
        tipo = 'npth' if 'TF.FileFunction,NonPlated,' in texto else 'pth' if 'TF.FileFunction,Plated,' in texto else None
        if not tipo or not texto.startswith('M48') or not texto.rstrip().endswith('M30') or 'METRIC' not in texto:
            problemas.append('Taladros con formato incompleto o no reconocido: ' + p.name)
            continue
        agujeros.add(tipo)
        if inv['taladros_' + tipo] and not re.search(r'^T\d+C[0-9.]', texto, re.M):
            problemas.append('No hay herramientas de taladro para ' + tipo)
        manifest.append({'archivo': p.name, 'tipo': 'excellon', 'funcion': tipo, 'bytes': len(datos), 'sha256': sha(datos)})
    for tipo in ('pth', 'npth'):
        if inv['taladros_' + tipo] and tipo not in agujeros:
            problemas.append('Faltan taladros ' + tipo)
    return manifest, problemas


def evaluar(inv, drc, problemas, paridad_disponible):
    bloqueos, pendientes = [], list(problemas)
    if not inv['huellas'] or not inv['elementos_contorno']:
        bloqueos.append('Faltan huellas o contorno Edge.Cuts')
    if drc is None:
        pendientes.append('No hay un DRC completo de esta versión')
    else:
        for campo, etiqueta in (('errores', 'errores DRC'), ('sin_conectar', 'conexiones pendientes'), ('paridad', 'diferencias con el esquemático')):
            if drc[campo]:
                bloqueos.append(f'{drc[campo]} {etiqueta}')
        if drc['avisos'] or drc['excluidos']:
            pendientes.append('Revisar advertencias y exclusiones de DRC, sin aceptarlas automáticamente')
    if not paridad_disponible:
        pendientes.append('No se comprobó la paridad con el esquemático')
    if inv['vias_especiales']:
        pendientes.append('Revisar procesos y taladros para vías ciegas, enterradas o microvías')
    pendientes.extend(['Definir y comprobar el pedido: servicio, stackup, cobre, espesor, acabado, cantidad y reglas del fabricante',
                       'Inspeccionar Gerbers y taladros en un visor: contorno cerrado, registro, agujeros, máscara y serigrafía',
                       'Revisar la previsualización y las advertencias de JLCPCB antes de confirmar el pedido'])
    return {'veredicto': 'NOT READY' if bloqueos else 'INCOMPLETE', 'bloqueos': bloqueos,
            'pendientes': pendientes, 'listo_para_pedir': False}


def _run(args):
    return subprocess.run([str(CLI), *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _exportar(args):
    r = _run(args)
    if r.returncode:
        raise ErrorFabricacion('KiCad no pudo exportar: ' + (r.stderr or r.stdout)[-1800:])


def preparar(actual, peticion, salida=SALIDA):
    board = (actual.get('archivos') or {}).get('pcb')
    if not board:
        raise ErrorFabricacion('Abre una placa para preparar su fabricación')
    board = Path(board).resolve()
    if str(board.parent) != peticion.get('proyecto_id'):
        raise ErrorFabricacion('El proyecto activo cambió')
    if not _lock.acquire(blocking=False):
        raise ErrorFabricacion('Ya se está preparando un paquete de fabricación')
    try:
        salida = Path(salida).resolve()
        salida.mkdir(parents=True, exist_ok=True)
        id_ = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='.preparando-', dir=salida) as tmp:
            destino = Path(tmp).resolve()
            assert destino.parent == salida
            fuentes = destino / 'fuentes'
            fuentes.mkdir()
            with bloquear(board.parent):
                rev = comprobar(board.parent, peticion.get('expected_revision'))
                originales = []
                for p in archivos(board.parent):
                    relativo = p.relative_to(board.parent)
                    datos = p.read_bytes()
                    atomico(fuentes / relativo, datos)
                    originales.append({'archivo': relativo.as_posix(), 'sha256': sha(datos)})
                comprobar(board.parent, rev)
            copia = fuentes / board.name
            inv = inventario(copia)
            gerbers = destino / 'gerbers'
            gerbers.mkdir()
            problemas, drc = [], None
            sch = copia.with_suffix('.kicad_sch')
            args = ['pcb', 'drc', '--format', 'json', '--severity-all', '--units', 'mm']
            if sch.is_file():
                args += ['--schematic-parity']
            try:
                datos = informe_json([*args, '-o', str(destino / 'drc.json'), str(copia)], destino / 'drc.json', _run,
                                     ('violations', 'unconnected_items', 'schematic_parity'))
                drc = resumen_drc(datos)
            except (RuntimeError, ValueError) as exc:
                problemas.append(str(exc))
            capas = [*inv['capas_cobre'], 'F.Mask', 'B.Mask', 'F.Silkscreen', 'B.Silkscreen', 'F.Paste', 'B.Paste', 'Edge.Cuts']
            try:
                _exportar(['pcb', 'export', 'gerbers', '-o', str(gerbers), '--layers', ','.join(capas), '--subtract-soldermask', str(copia)])
                _exportar(['pcb', 'export', 'drill', '-o', str(gerbers), '--format', 'excellon', '--drill-origin', 'absolute',
                           '--excellon-units', 'mm', '--excellon-zeros-format', 'decimal', '--excellon-oval-format', 'alternate',
                           '--excellon-separate-th', '--generate-map', '--map-format', 'pdf', str(copia)])
                manifest, faltantes = validar_salidas(gerbers, inv)
                problemas.extend(faltantes)
            except (ErrorFabricacion, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                manifest = []
                problemas.append(str(exc))
            resultado = {'id': id_, 'proyecto_id': str(board.parent), 'revision': rev, 'placa': board.name,
                         'creado': datetime.now(timezone.utc).isoformat(), 'alcance': 'PCB sin montaje de componentes',
                         'inventario': inv, 'drc': drc, 'archivos': manifest, 'fuentes': originales,
                         'referencias': [{'url': GUIA, 'consultado': '2026-09-16'}],
                         **evaluar(inv, drc, problemas, sch.is_file())}
            if revision(board.parent) != rev:
                resultado['pendientes'].append('El diseño cambió durante la exportación; preparar otro paquete')
                if not resultado['bloqueos']:
                    resultado['veredicto'] = 'INCOMPLETE'
            # Draft only, and only when automatic checks have no blockers or missing outputs.
            resultado['zip'] = None
            if not resultado['bloqueos'] and not problemas and revision(board.parent) == rev:
                resultado['zip'] = 'GERBERS-PARA-REVISION.zip'
                with zipfile.ZipFile(destino / resultado['zip'], 'w', zipfile.ZIP_DEFLATED) as z:
                    for m in manifest:
                        z.write(gerbers / m['archivo'], m['archivo'])
            atomico(destino / 'informe.json', json.dumps(resultado, ensure_ascii=False, indent=2).encode())
            destino.rename(salida / id_)
        return {'ok': True, **resultado}
    finally:
        _lock.release()


def consultar(id_, salida=SALIDA):
    if not isinstance(id_, str) or not re.fullmatch('[0-9a-f]{32}', id_):
        raise ErrorFabricacion('Identificador de paquete inválido')
    ruta = Path(salida) / id_ / 'informe.json'
    data = json.loads(ruta.read_text(encoding='utf-8'))
    data['vigente'] = revision(data['proyecto_id']) == data['revision']
    if not data['vigente']:
        data['zip'] = None
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--placa', type=Path, required=True)
    a = parser.parse_args()
    p = a.placa.resolve()
    print(json.dumps(preparar({'archivos': {'pcb': str(p)}}, {'proyecto_id': str(p.parent),
                     'expected_revision': revision(p.parent)}), ensure_ascii=False, indent=2))
