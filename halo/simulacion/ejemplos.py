"""Bancos reproducibles. El 555 es funcional/ideal, no un modelo de fabricante."""
from copy import deepcopy


MODELO_555 = """* Halo: astable 555 ideal. TRIG unido a THRESH, RESET a VCC.
* Sólo temporización: no modela límites, consumo ni caída de salida del NE555.
.subckt HALO555_ASTABLE g trig out reset ctrl threshold discharge vcc
Bnorm sense g V=V(threshold,g)/max(V(vcc,g),0.1)
Sdis discharge g sense g timing_switch
Sstate q g sense g timing_switch
Rpull vcc q 1000
Bout out g V=V(q,g)
Rupper vcc ctrl 5000
Rlower ctrl g 10000
.model timing_switch SW(Ron=1 Roff=1e12 Vt=0.5 Vh=0.1666666667)
.ends HALO555_ASTABLE
"""


def rc(tipo='tran'):
    componentes = {'V1': {'valor': '5V', 'pines': {'1': 'entrada', '2': 'GND'}},
                   'R1': {'valor': '1k', 'pines': {'1': 'entrada', '2': 'salida'}},
                   'C1': {'valor': '1uF', 'pines': {'1': 'salida', '2': 'GND'}}}
    config = {'version': 1, 'nombre': 'RC · carga de un condensador', 'tierra': 'GND',
              'componentes': {'V1': {'tipo': 'V', 'pines': ['1', '2'], 'dc': 5, 'ac': 1},
                              'R1': {'tipo': 'R', 'pines': ['1', '2'], 'valor': 1000},
                              'C1': {'tipo': 'C', 'pines': ['1', '2'], 'valor': 1e-6, 'inicial': 0}},
              'analisis': {'tipo': 'tran', 'paso': 1e-5, 'fin': 0.005, 'uic': True},
              'sondas': [{'id': 'entrada', 'red': 'entrada'}, {'id': 'salida', 'red': 'salida'}],
              'medidas': [{'id': 'tension_una_tau', 'sonda': 'salida', 'metrica': 'valor_en', 'en': 0.001,
                           'min': 3.15, 'max': 3.17},
                          {'id': 'tension_final', 'sonda': 'salida', 'metrica': 'final', 'min': 4.96, 'max': 4.98}],
              'limitaciones': ['Ejemplo independiente: no representa el proyecto abierto.',
                               'R y C ideales, sin tolerancias ni parasíticos.']}
    if tipo == 'ac':
        config['nombre'] = 'RC · respuesta en frecuencia'
        config['analisis'] = {'tipo': 'ac', 'inicio': 1, 'fin': 10000, 'puntos': 80}
        config['medidas'] = [{'id': 'ganancia_corte', 'sonda': 'salida', 'metrica': 'valor_en',
                              'en': 159.154943, 'min': 0.706, 'max': 0.708}]
    elif tipo == 'op':
        config['nombre'] = 'RC · punto de operación'
        config['analisis'] = {'tipo': 'op'}
        config['medidas'] = [{'id': 'tension_salida', 'sonda': 'salida', 'metrica': 'final', 'min': 4.999, 'max': 5.001}]
    return config, componentes


def config_555():
    return {'version': 1, 'nombre': '555 · temporización astable ideal', 'tierra': 'GND',
            'componentes': {
                'C1': {'tipo': 'C', 'pines': ['1', '2'], 'valor': 10e-6, 'inicial': 0},
                'C2': {'tipo': 'C', 'pines': ['1', '2'], 'valor': 10e-9},
                'C3': {'tipo': 'C', 'pines': ['1', '2'], 'valor': 100e-9},
                'R1': {'tipo': 'R', 'pines': ['1', '2'], 'valor': 1000},
                'R2': {'tipo': 'R', 'pines': ['1', '2'], 'valor': 68000},
                'R3': {'tipo': 'R', 'pines': ['1', '2'], 'valor': 470},
                'D1': {'tipo': 'D', 'pines': ['2', '1'], 'modelo': 'LED_IDEAL'},
                'J1': {'tipo': 'omitir', 'motivo': 'Conector sustituido por fuente de banco de 5 V'},
                'U1': {'tipo': 'X', 'pines': ['1', '2', '3', '4', '5', '6', '7', '8'], 'modelo': 'HALO555_ASTABLE'}},
            'fuentes': [{'positivo': '+5V', 'dc': 5}],
            'modelos_inline': [MODELO_555, '.model LED_IDEAL D(Is=1e-20 N=2 Rs=5)'],
            'analisis': {'tipo': 'tran', 'paso': 0.001, 'fin': 5, 'uic': True},
            'sondas': [{'id': 'salida', 'red': 'PULSO'}, {'id': 'condensador', 'red': 'Net-(U1-THRES)'}],
            'medidas': [{'id': 'frecuencia', 'sonda': 'salida', 'metrica': 'frecuencia',
                         'umbral': 2.5, 'desde': 1.5, 'min': 1.04, 'max': 1.07}],
            'limitaciones': ['555 funcional ideal, válido sólo para este astable: TRIG=THRESH y RESET=VCC.',
                             'No verifica consumo, corriente admisible, carga ni caída de salida del NE555 real.',
                             'LED genérico educativo, sin validación de fabricante.']}


def ejemplo(nombre):
    if nombre in ('rc', 'rc_ac', 'rc_op'):
        return rc({'rc': 'tran', 'rc_ac': 'ac', 'rc_op': 'op'}[nombre])
    if nombre != '555':
        raise ValueError('Ejemplo desconocido')
    config = config_555()
    conexiones = {
        '+5V': [('C3', '1'), ('J1', '1'), ('R1', '1'), ('U1', '4'), ('U1', '8')],
        'GND': [('C1', '2'), ('C2', '2'), ('C3', '2'), ('D1', '1'), ('J1', '2'), ('U1', '1')],
        'Net-(D1-A)': [('D1', '2'), ('R3', '2')],
        'Net-(U1-CONT)': [('C2', '1'), ('U1', '5')],
        'Net-(U1-DISCH)': [('R1', '2'), ('R2', '1'), ('U1', '7')],
        'Net-(U1-THRES)': [('C1', '1'), ('R2', '2'), ('U1', '2'), ('U1', '6')],
        'PULSO': [('R3', '1'), ('U1', '3')]}
    componentes = {ref: {'valor': ref, 'pines': {}} for ref in config['componentes']}
    for red, pines in conexiones.items():
        for ref, pin in pines:
            componentes[ref]['pines'][pin] = red
    config['limitaciones'].insert(0, 'Ejemplo independiente: no representa el proyecto abierto.')
    return deepcopy(config), componentes
