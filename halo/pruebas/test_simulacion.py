"""Pruebas de simulación: motor real + referencias analíticas + fallos de ciclo de vida."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from halo.kicad.proyecto import revision
from halo.simulacion.ejemplos import ejemplo, config_555
from halo.simulacion.medidas import medir
from halo.simulacion.preparar import ErrorSimulacion, biblioteca, compilar, exportar, huella
from halo.simulacion.servicio import Servicio, guardar_json, vigencia, CONFIG

REPO = Path(__file__).resolve().parents[2]


class Simulacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='halo-sim-test-')
        self.addCleanup(self.tmp.cleanup)
        self.servicio = Servicio(Path(self.tmp.name) / 'trabajos')

    def esperar(self, id_):
        limite = time.monotonic() + 15
        while time.monotonic() < limite:
            r = self.servicio.leer(id_)
            if r['estado'] not in ('ejecutando', 'cancelando'):
                return r
            time.sleep(.04)
        self.servicio.cancelar(id_)
        self.fail('El trabajo no terminó')

    def ejecutar(self, nombre):
        m = self.servicio.preparar({}, {'ejemplo': nombre})
        self.servicio.ejecutar(m['id'])
        r = self.esperar(m['id'])
        self.assertEqual(r['estado'], 'completado', r.get('error'))
        return r

    def test_rc_transitorio_referencia_analitica(self):
        r = self.ejecutar('rc')
        medidas = r['resultado']['medidas']
        self.assertAlmostEqual(medidas[0]['valor'], 5 * (1 - math.exp(-1)), delta=.001)
        self.assertAlmostEqual(medidas[1]['valor'], 5 * (1 - math.exp(-5)), delta=.0001)
        self.assertEqual(r['resultado']['criterios'], 'cumple')
        self.assertEqual(r['vigencia'], 'ejemplo')
        self.assertTrue((self.servicio.ruta(r['id']) / 'crudo.json').is_file())

    def test_rc_ac_referencia_analitica(self):
        r = self.ejecutar('rc_ac')
        self.assertAlmostEqual(r['resultado']['medidas'][0]['valor'], 1 / math.sqrt(2), delta=.001)
        self.assertEqual(r['resultado']['series'][1]['representacion'], 'magnitud')

    def test_rc_operacion(self):
        r = self.ejecutar('rc_op')
        self.assertAlmostEqual(r['resultado']['medidas'][0]['valor'], 5, places=5)

    def test_555_funcional_temporizacion(self):
        r = self.ejecutar('555')
        esperado = 1 / (math.log(2) * (1000 + 2 * 68000) * 10e-6)
        self.assertAlmostEqual(r['resultado']['medidas'][0]['valor'], esperado, delta=.002)
        self.assertTrue(any('ideal' in s for s in r['limitaciones']))

    def test_sonda_inexistente_no_pasa(self):
        m = self.servicio.preparar({}, {'ejemplo': 'rc'})
        p = self.servicio.ruta(m['id']) / 'entrada.json'
        d = json.loads(p.read_text(encoding='utf-8'))
        d['vectores'] = ['v(no_existe)']
        guardar_json(p, d)
        with self.assertRaisesRegex(ErrorSimulacion, 'instantánea'):
            self.servicio.ejecutar(m['id'])
        self.assertNotIn('resultado', self.servicio.leer(m['id']))

    def test_modelo_ausente_falla_en_motor(self):
        c, n = ejemplo('555')
        c['componentes']['U1']['modelo'] = 'NO_EXISTE'
        with patch('halo.simulacion.servicio.ejemplo', return_value=(c, n)):
            m = self.servicio.preparar({}, {'ejemplo': '555'})
        self.servicio.ejecutar(m['id'])
        self.assertEqual(self.esperar(m['id'])['estado'], 'error')

    def proceso_lento(self):
        popen = subprocess.Popen
        return lambda *args, **kwargs: popen([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)

    def test_cancelacion_termina_proceso_real(self):
        m = self.servicio.preparar({}, {'ejemplo': 'rc'})
        with patch('halo.simulacion.servicio.subprocess.Popen', self.proceso_lento()):
            self.servicio.ejecutar(m['id'])
        proc = self.servicio.procesos[m['id']]
        self.servicio.cancelar(m['id'])
        self.assertEqual(self.esperar(m['id'])['estado'], 'cancelado')
        self.assertIsNotNone(proc.poll())

    def test_timeout_termina_proceso_real(self):
        m = self.servicio.preparar({}, {'ejemplo': 'rc'})
        with patch('halo.simulacion.servicio.subprocess.Popen', self.proceso_lento()):
            self.servicio.ejecutar(m['id'], timeout=1)
        proc = self.servicio.procesos[m['id']]
        self.assertEqual(self.esperar(m['id'])['estado'], 'timeout')
        self.assertIsNotNone(proc.poll())

    def test_fallo_al_publicar_arranque_no_deja_proceso(self):
        m = self.servicio.preparar({}, {'ejemplo': 'rc'})
        lento, capturados = self.proceso_lento(), []
        def lanzar(*args, **kwargs):
            proc = lento(*args, **kwargs)
            capturados.append(proc)
            return proc
        with patch('halo.simulacion.servicio.subprocess.Popen', lanzar), patch(
                'halo.simulacion.servicio.guardar_json', side_effect=OSError('disco lleno')):
            with self.assertRaises(OSError):
                self.servicio.ejecutar(m['id'])
        self.assertEqual(len(capturados), 1)
        self.assertIsNotNone(capturados[0].poll())
        self.assertFalse(self.servicio.procesos)

    def test_rechaza_ejecutar_dos_veces(self):
        r = self.ejecutar('rc')
        with self.assertRaises(ErrorSimulacion):
            self.servicio.ejecutar(r['id'])

    def test_comparacion(self):
        a, b = self.ejecutar('rc'), self.ejecutar('rc')
        c = self.servicio.comparar(b['id'], a['id'])
        self.assertEqual(c['cambios'][0]['delta'], 0)

    def test_rechaza_directivas_activas(self):
        for texto in ('.control\nshell echo mal\n.endc', '.include otro.lib', '.lib otro.lib a', '.osdi plugin.osdi', '.end'):
            with self.subTest(texto=texto), self.assertRaises(ErrorSimulacion):
                biblioteca(texto)

    def test_rechaza_modelos_y_pines_incompletos(self):
        for cambio in ('faltante', 'pines', 'tierra', 'valor', 'sonda'):
            c, n = ejemplo('rc')
            if cambio == 'faltante': del c['componentes']['R1']
            if cambio == 'pines': c['componentes']['R1']['pines'] = ['1', '3']
            if cambio == 'tierra': c['tierra'] = 'no existe'
            if cambio == 'valor': c['componentes']['R1']['valor'] = '1M'
            if cambio == 'sonda': c['sondas'][0]['red'] = 'no existe'
            with self.subTest(cambio=cambio), self.assertRaises(ErrorSimulacion):
                compilar(c, n, [])

    def test_frecuencia_insuficiente_no_verificada(self):
        self.assertIsNone(medir([0, 1, 2], [0, 5, 5], {'metrica': 'frecuencia', 'umbral': 2.5}))

    def test_modelo_y_config_modificados_invalidan(self):
        carpeta = Path(self.tmp.name) / 'proyecto'
        carpeta.mkdir()
        modelo = carpeta / 'modelo.lib'
        modelo.write_text('.model d D', encoding='utf-8')
        m = {'proyecto_id': str(carpeta), 'revision': revision(carpeta),
             'dependencias': {'modelo.lib': huella(modelo.read_bytes()), CONFIG: None}}
        self.assertEqual(vigencia(m), 'vigente')
        modelo.write_text('.model d D(Is=1e-12)', encoding='utf-8')
        self.assertEqual(vigencia(m), 'obsoleto')
        modelo.write_text('.model d D', encoding='utf-8')
        guardar_json(carpeta / CONFIG, {'version': 1})
        self.assertEqual(vigencia(m), 'obsoleto')

    def test_identificador_no_permite_rutas(self):
        with self.assertRaises(ErrorSimulacion):
            self.servicio.leer('../otro')

    def test_kicad_555_exportado_sin_modificar_diseno(self):
        sch = REPO / 'halo/proyectos/parpadeo555/parpadeo555.kicad_sch'
        antes = revision(sch.parent)
        c = config_555()
        componentes = exportar(sch, Path(self.tmp.name) / 'netlist.xml')
        for ref, d in componentes.items():
            c['componentes'][ref]['valor_esquematico'] = d['valor']
        actual = {'archivos': {'esquematico': str(sch)}}
        m = self.servicio.preparar(actual, {'proyecto_id': str(sch.parent), 'expected_revision': antes, 'config': c})
        self.servicio.ejecutar(m['id'])
        r = self.esperar(m['id'])
        self.assertEqual(r['estado'], 'completado', r.get('error'))
        self.assertEqual(r['vigencia'], 'vigente')
        self.assertEqual(r['resultado']['criterios'], 'cumple')
        self.assertEqual(revision(sch.parent), antes)
        # Material change in a component value cannot silently reuse an old mapping.
        c['componentes']['R1']['valor_esquematico'] = '99k'
        with self.assertRaisesRegex(ErrorSimulacion, 'valor_esquematico'):
            self.servicio.preparar(actual, {'proyecto_id': str(sch.parent), 'expected_revision': antes, 'config': c})


if __name__ == '__main__':
    unittest.main(verbosity=2)
