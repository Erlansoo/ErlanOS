"""Compila una especificación JSON a SPICE usando conexiones exportadas por KiCad.

No infiere modelos de un nombre comercial, ni cambia ningún archivo KiCad.
Los pines se declaran en el orden del modelo, nunca en el orden gráfico.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..kicad.oraculo import _run
from ..kicad.verificador import ejecutar


class ErrorSimulacion(ValueError):
    pass


def huella(datos):
    return hashlib.sha256(datos if isinstance(datos, bytes) else
                          json.dumps(datos, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def numero(valor, nombre, positivo=False):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        raise ErrorSimulacion(f'{nombre}: usa un número finito en unidades SI')
    if positivo and valor <= 0:
        raise ErrorSimulacion(f'{nombre} debe ser positivo')
    return float(valor)


def identificador(valor):
    if not isinstance(valor, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', valor):
        raise ErrorSimulacion(f'Identificador inválido: {valor!r}')
    return valor


def biblioteca(texto):
    """Bibliotecas autocontenidas. Sin .control, .include, archivos ni código cargable."""
    if not isinstance(texto, str) or len(texto) > 500000 or '\x00' in texto or '`' in texto:
        raise ErrorSimulacion('Biblioteca inválida o demasiado grande')
    permitidas = {'.model', '.subckt', '.ends', '.param', '.func'}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith('*'):
            if linea.startswith('*#'):   # en ngspice interactivo «*#» al inicio de línea es un comando, no un comentario
                raise ErrorSimulacion('Línea de control de ngspice no admitida: ' + linea[:100])
            continue
        if linea.startswith('.'):
            if linea.split()[0].lower() not in permitidas:
                raise ErrorSimulacion('Directiva de biblioteca no admitida: ' + linea[:100])
        elif linea[0].upper() not in 'RCLVIDQMXBEFGHSWK+' or ';' in linea:
            raise ErrorSimulacion('Elemento de biblioteca no admitido: ' + linea[:100])
    return texto


def cargar_modelos(carpeta, config):
    textos, dependencias = [], {}
    for nombre in config.get('modelos', []):
        if not isinstance(nombre, str):
            raise ErrorSimulacion('Cada modelo debe ser una ruta relativa')
        ruta = (carpeta / nombre).resolve()
        if not ruta.is_relative_to(carpeta.resolve()) or not ruta.is_file():
            raise ErrorSimulacion('Modelo fuera del proyecto o inexistente: ' + nombre)
        if ruta.stat().st_size > 500000:
            raise ErrorSimulacion('Modelo demasiado grande: ' + nombre)
        datos = ruta.read_bytes()
        dependencias[nombre] = huella(datos)
        textos.append(biblioteca(datos.decode('utf-8-sig')))
    for texto in config.get('modelos_inline', []):
        textos.append(biblioteca(texto))
    return textos, dependencias


def exportar(sch, destino):
    nueva = ejecutar(['sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(destino), str(sch)],
                     destino, _run)
    nueva.replace(destino)
    raiz = ET.parse(destino).getroot()
    if raiz.tag != 'export' or raiz.find('nets') is None or raiz.find('components') is None:
        raise ErrorSimulacion('Netlist KiCad incompleta')
    if len(raiz.findall('./design/sheet')) > 1:
        raise ErrorSimulacion('Esta primera integración requiere una sola hoja; falta versionar todas las hojas jerárquicas')
    componentes = {}
    for c in raiz.findall('./components/comp'):
        ref = c.attrib['ref']
        if ref in componentes:
            raise ErrorSimulacion('Referencia repetida en la netlist: ' + ref)
        componentes[ref] = {'valor': c.findtext('value'), 'pines': {}}
    for red in raiz.findall('./nets/net'):
        for pin in red.findall('node'):
            ref = pin.attrib['ref']
            if ref in componentes:
                componentes[ref]['pines'][pin.attrib['pin']] = red.attrib['name']
    return componentes


def analisis(a):
    tipo = a.get('tipo')
    if tipo == 'op':
        return 'op', None, None, {'nombre': 'Punto de operación', 'unidad': '', 'log': False}
    if tipo == 'tran':
        paso = numero(a.get('paso'), 'paso', True)
        fin = numero(a.get('fin'), 'fin', True)
        if paso > fin or fin / paso > 20000:
            raise ErrorSimulacion('Transitorio: entre 1 y 20000 pasos solicitados')
        # tmax bounds interpolation error; uic must be a deliberate user choice.
        uic = ' uic' if a.get('uic') is True else ''
        return f'tran {paso:.12g} {fin:.12g} 0 {paso:.12g}{uic}', 'time', fin, {'nombre': 'Tiempo', 'unidad': 's', 'log': False}
    if tipo == 'ac':
        inicio = numero(a.get('inicio'), 'inicio', True)
        fin = numero(a.get('fin'), 'fin', True)
        puntos = a.get('puntos', 50)
        if isinstance(puntos, bool) or not isinstance(puntos, int) or not 1 <= puntos <= 1000 or fin <= inicio:
            raise ErrorSimulacion('AC: revisa frecuencias y puntos por década')
        if math.log10(fin / inicio) * puntos > 20000:
            raise ErrorSimulacion('Demasiados puntos AC')
        return f'ac dec {puntos} {inicio:.12g} {fin:.12g}', 'frequency', fin, {'nombre': 'Frecuencia', 'unidad': 'Hz', 'log': True}
    raise ErrorSimulacion('Análisis admitidos: op, tran, ac')


def validar_config(config):
    """Validate the JSON shape before exporting files or accessing model paths."""
    if not isinstance(config, dict) or type(config.get('version')) is not int or config['version'] != 1:
        raise ErrorSimulacion('Configuración de simulación: se requiere version 1')
    for clave in ('sondas', 'medidas', 'fuentes', 'modelos', 'modelos_inline', 'limitaciones'):
        if not isinstance(config.get(clave, []), list):
            raise ErrorSimulacion(f'{clave} debe ser una lista')
    if not isinstance(config.get('analisis', {}), dict):
        raise ErrorSimulacion('analisis debe ser un objeto')
    asignaciones = config.get('componentes', {})
    if not isinstance(asignaciones, dict) or not asignaciones:
        raise ErrorSimulacion('Faltan componentes')
    if any(not isinstance(m, dict) for m in asignaciones.values()) or any(
            not isinstance(m, dict) for clave in ('sondas', 'medidas', 'fuentes') for m in config.get(clave, [])):
        raise ErrorSimulacion('Componentes, sondas, medidas y fuentes deben ser objetos')
    if not all(isinstance(s, str) for s in config.get('limitaciones', [])):
        raise ErrorSimulacion('Las limitaciones deben ser textos')


def compilar(config, componentes, textos):
    validar_config(config)
    asignaciones = config['componentes']
    if not componentes:
        raise ErrorSimulacion('Faltan componentes exportados')
    desconocidos = set(asignaciones) - set(componentes)
    faltan = set(componentes) - set(asignaciones)
    if desconocidos or faltan:
        raise ErrorSimulacion(f'Asignaciones incompletas. Faltan: {sorted(faltan)}. Desconocidos: {sorted(desconocidos)}')
    redes = sorted({red for c in componentes.values() for red in c['pines'].values()})
    tierra = config.get('tierra')
    if tierra not in redes:
        raise ErrorSimulacion('Declara tierra con el nombre exacto de una red exportada')
    nodos = {r: '0' if r == tierra else f'n{i}' for i, r in enumerate(redes)}
    lineas = ['Halo - circuito reproducible']
    elementos, exclusiones = {}, []
    limites = list(config.get('limitaciones', []))
    for ref, c in componentes.items():
        identificador(ref)
        m = asignaciones[ref]
        tipo = m.get('tipo')
        if tipo == 'omitir':
            if not isinstance(m.get('motivo'), str) or not m['motivo'].strip():
                raise ErrorSimulacion(f'{ref}: excluir requiere motivo explícito')
            exclusiones.append({'ref': ref, 'motivo': m['motivo']})
            continue
        if tipo not in ('R', 'C', 'L', 'V', 'I', 'D', 'X'):
            raise ErrorSimulacion(f'{ref}: tipo no soportado; asigna un modelo o una exclusión explícita')
        pines = m.get('pines', [])
        if not isinstance(pines, list) or not pines or len(set(map(str, pines))) != len(pines):
            raise ErrorSimulacion(f'{ref}: declara el orden de pines del modelo')
        if tipo != 'X' and len(pines) != 2:
            raise ErrorSimulacion(f'{ref}: {tipo} requiere dos pines')
        try:
            conexiones = [nodos[c['pines'][str(p)]] for p in pines]
        except KeyError as exc:
            raise ErrorSimulacion(f'{ref}: pin sin conexión exportada: {exc}') from exc
        no_cubiertos = set(c['pines']) - set(map(str, pines))
        if no_cubiertos:
            raise ErrorSimulacion(f'{ref}: el modelo deja pines sin representar: {sorted(no_cubiertos)}')
        nombre = tipo + '_' + ref
        elementos[ref] = nombre.lower()
        if tipo in ('R', 'C', 'L'):
            valor = f"{numero(m.get('valor'), ref + '.valor', True):.12g}"
            if tipo in ('C', 'L') and 'inicial' in m:
                valor += f" ic={numero(m['inicial'], 'inicial'):.12g}"
        elif tipo in ('V', 'I'):
            valor = f"DC {numero(m.get('dc', 0), ref + '.dc'):.12g}"
            if 'ac' in m:
                valor += f" AC {numero(m['ac'], 'ac'):.12g}"
            if 'pulso' in m:
                pulso = m['pulso']
                if not isinstance(pulso, list) or len(pulso) != 7:
                    raise ErrorSimulacion('Pulso: [bajo, alto, retardo, subida, bajada, ancho, periodo]')
                valores = [numero(v, 'pulso') for v in pulso]
                if any(v < 0 for v in valores[2:]) or any(v <= 0 for v in valores[3:]) or valores[5] > valores[6]:
                    raise ErrorSimulacion('Tiempos de pulso inválidos')
                valor += ' PULSE(' + ' '.join(f'{v:.12g}' for v in valores) + ')'
        else:
            valor = identificador(m.get('modelo'))
        lineas.append(' '.join([nombre, *conexiones, valor]))
    # Testbench sources refer to real net names; never pretend they exist in the schematic.
    for i, fuente in enumerate(config.get('fuentes', [])):
        try:
            mas, menos = nodos[fuente['positivo']], nodos[fuente.get('negativo', tierra)]
        except KeyError as exc:
            raise ErrorSimulacion(f'Fuente: red inexistente {exc}') from exc
        lineas.append(f"V_banco{i} {mas} {menos} DC {numero(fuente.get('dc'), 'fuente.dc'):.12g} AC {numero(fuente.get('ac', 0), 'fuente.ac'):.12g}")
    if not elementos:
        raise ErrorSimulacion('No hay elementos simulables')
    sondas = []
    for s in config.get('sondas', []):
        identificador(s.get('id'))
        if 'red' in s:
            if s['red'] not in nodos or nodos[s['red']] == '0':
                raise ErrorSimulacion('Sonda: red inexistente o tierra (0 V)')
            vector, unidad = f"v({nodos[s['red']]})", 'V'
            refs = [ref for ref, c in componentes.items() if s['red'] in c['pines'].values()]
        elif 'corriente' in s:
            ref = s['corriente']
            if ref not in elementos or asignaciones[ref]['tipo'] != 'V':
                raise ErrorSimulacion('La corriente se mide a través de una fuente V explícita')
            vector, unidad, refs = f'i({elementos[ref]})', 'A', [ref]
        else:
            raise ErrorSimulacion('Cada sonda debe indicar red o corriente')
        sondas.append({**s, 'vector': vector, 'unidad': unidad, 'referencias': refs})
    ids = [s['id'] for s in sondas]
    if not 1 <= len(sondas) <= 8 or len(ids) != len(set(ids)):
        raise ErrorSimulacion('Declara entre 1 y 8 sondas con identificadores únicos')
    medidas = config.get('medidas', [])
    if len(medidas) > 50:
        raise ErrorSimulacion('Máximo 50 mediciones')
    for m in medidas:
        identificador(m.get('id'))
        if m.get('sonda') not in ids or m.get('metrica') not in ('min', 'max', 'final', 'pico_a_pico', 'valor_en', 'frecuencia'):
            raise ErrorSimulacion('Medición desconocida o sonda ausente')
        for clave in ('min', 'max', 'desde', 'hasta', 'en', 'umbral'):
            if clave in m:
                numero(m[clave], 'medida.' + clave)
        requerido = {'valor_en': 'en', 'frecuencia': 'umbral'}.get(m['metrica'])
        if requerido and requerido not in m:
            raise ErrorSimulacion('Falta medida.' + requerido)
        if m.get('min', -math.inf) > m.get('max', math.inf) or m.get('desde', -math.inf) > m.get('hasta', math.inf):
            raise ErrorSimulacion('Intervalo de medición inválido')
        if m['metrica'] == 'frecuencia' and config.get('analisis', {}).get('tipo') != 'tran':
            raise ErrorSimulacion('La frecuencia por cruces sólo aplica a transitorios')
    if len({m['id'] for m in medidas}) != len(medidas):
        raise ErrorSimulacion('Identificadores de medición repetidos')
    comando, eje, fin, eje_info = analisis(config.get('analisis', {}))
    for texto in textos:
        lineas.extend(biblioteca(texto).splitlines())
    lineas += ['.save ' + ' '.join(s['vector'] for s in sondas), '.end']
    return {'deck': '\n'.join(lineas) + '\n', 'comando': comando, 'eje': eje, 'fin': fin,
            'eje_info': eje_info, 'sondas': sondas, 'medidas': medidas,
            'exclusiones': exclusiones, 'limitaciones': limites, 'nodos': nodos,
            'vectores': [s['vector'] for s in sondas]}


def plantilla(componentes):
    """Deliberately incomplete: an unknown model must block, not silently disappear."""
    return {'version': 1, 'nombre': 'Banco de pruebas', 'tierra': '',
            'componentes': {ref: {'tipo': 'ASIGNAR', 'pines': sorted(c['pines']),
                                  'valor_esquematico': c['valor']} for ref, c in componentes.items()},
            'fuentes': [], 'modelos': [], 'analisis': {'tipo': 'tran', 'paso': 0.00001, 'fin': 0.01},
            'sondas': [], 'medidas': []}
