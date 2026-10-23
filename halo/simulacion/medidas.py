"""Mediciones calculadas sobre todos los puntos, nunca sobre la gráfica reducida."""
import math
from bisect import bisect_left, bisect_right
from itertools import chain, islice


def interpolar(eje, valores, objetivo):
    """Interpolación lineal sin extrapolación; eje estrictamente creciente."""
    i = bisect_left(eje, objetivo)
    if i < len(eje) and eje[i] == objetivo:
        return valores[i]
    if i == 0 or i == len(eje):
        return None
    peso = (objetivo - eje[i - 1]) / (eje[i] - eje[i - 1])
    return valores[i - 1] * (1 - peso) + valores[i] * peso


def medir(eje, valores, especificacion):
    desde = especificacion.get('desde', -math.inf)
    hasta = especificacion.get('hasta', math.inf)
    if not eje or desde > hasta:
        return None
    metrica = especificacion['metrica']
    if metrica == 'valor_en':
        objetivo = especificacion['en']
        return interpolar(eje, valores, objetivo) if desde <= objetivo <= hasta else None
    desde, hasta = max(desde, eje[0]), min(hasta, eje[-1])
    if desde > hasta:
        return None
    inicio, fin = bisect_right(eje, desde), bisect_left(eje, hasta)
    primero, ultimo = interpolar(eje, valores, desde), interpolar(eje, valores, hasta)

    def ys():
        return chain((primero,), (valores[i] for i in range(inicio, max(inicio, fin))), (ultimo,))

    if metrica == 'min':
        return min(ys())
    if metrica == 'max':
        return max(ys())
    if metrica == 'final':
        return ultimo
    if metrica == 'pico_a_pico':
        return max(ys()) - min(ys())
    if metrica == 'frecuencia':
        umbral = especificacion['umbral']
        cantidad, primer_cruce, ultimo_cruce = 0, None, None
        x0, y0 = desde, primero
        puntos = chain(((eje[i], valores[i]) for i in range(inicio, max(inicio, fin))), ((hasta, ultimo),))
        for x1, y1 in puntos:
            if y0 < umbral <= y1 and x1 > x0:
                ultimo_cruce = x0 + (umbral - y0) * (x1 - x0) / (y1 - y0)
                if primer_cruce is None:
                    primer_cruce = ultimo_cruce
                cantidad += 1
            x0, y0 = x1, y1
        return (cantidad - 1) / (ultimo_cruce - primer_cruce) if cantidad >= 2 else None
    raise ValueError(f'Métrica desconocida: {metrica}')


def resumir(datos, preparado):
    eje, series = datos['eje'], []
    if not eje or not all(math.isfinite(x) for x in eje) or any(a >= b for a, b in zip(eje, islice(eje, 1, None))):
        raise ValueError('Eje vacío, no finito o no estrictamente creciente')
    por_id, unidades, cache = {}, {}, {}
    for s in preparado['sondas']:
        v = datos['vectores'][s['vector']]
        if len(v['real']) != len(eje) or (v['imag'] is not None and len(v['imag']) != len(eje)):
            raise ValueError('Longitudes de vectores inconsistentes')
        if s['vector'] in cache:
            ys, resumen = cache[s['vector']]
            por_id[s['id']], unidades[s['id']] = ys, s['unidad']
            series.append({**s, **resumen})
            continue
        ys = [math.hypot(r, i) for r, i in zip(v['real'], v['imag'])] if v['imag'] is not None else v['real']
        if not all(math.isfinite(y) for y in ys):
            raise ValueError('Datos no finitos en la señal')
        por_id[s['id']] = ys
        unidades[s['id']] = s['unidad']
        # Extremos de cada bloque: no ocultar picos al reducir para la interfaz.
        paso = max(1, math.ceil(len(eje) / 750))
        if len(eje) <= 1502:
            indices = range(len(eje))
        else:
            indices = {0, len(eje) - 1}
            for inicio in range(0, len(eje), paso):
                rango = range(inicio, min(inicio + paso, len(eje)))
                indices.add(min(rango, key=ys.__getitem__))
                indices.add(max(rango, key=ys.__getitem__))
            indices = sorted(indices)
        resumen = {'puntos': [[eje[i], ys[i]] for i in indices],
                       'min': min(ys), 'max': max(ys), 'final': ys[-1],
                       'representacion': 'magnitud' if v['imag'] is not None else 'real'}
        cache[s['vector']] = ys, resumen
        series.append({**s, **resumen})
    medidas = []
    for m in preparado['medidas']:
        valor = medir(eje, por_id[m['sonda']], m)
        if valor is not None and not math.isfinite(valor):
            valor = None
        if valor is None:
            estado = 'no_verificado'
        elif 'min' not in m and 'max' not in m:
            estado = 'medido'
        else:
            estado = 'cumple' if m.get('min', -math.inf) <= valor <= m.get('max', math.inf) else 'no_cumple'
        unidad = 'Hz' if m['metrica'] == 'frecuencia' else unidades[m['sonda']]
        medidas.append({**m, 'valor': valor, 'estado': estado, 'unidad': unidad})
    estados = {m['estado'] for m in medidas}
    conclusion = ('no_cumple' if 'no_cumple' in estados else 'no_verificado' if 'no_verificado' in estados
                  else 'cumple' if 'cumple' in estados else 'sin_criterios')
    return {'series': series, 'medidas': medidas, 'criterios': conclusion,
            'puntos': len(eje), 'eje': preparado['eje_info'], 'motor': datos['motor'], 'log': datos['log']}
