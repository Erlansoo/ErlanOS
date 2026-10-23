"""Regresiones numéricas, integridad y trabajo redundante en simulación."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from halo.simulacion.medidas import medir, resumir
from halo.simulacion.preparar import ErrorSimulacion, validar_config
from halo.simulacion.servicio import Servicio, guardar_json


class Logica(unittest.TestCase):
    def test_interpolacion_entre_muestras_y_ventana_estrecha(self):
        for metrica, esperado in [('min', 4), ('max', 6), ('final', 6), ('pico_a_pico', 2)]:
            with self.subTest(metrica=metrica):
                self.assertAlmostEqual(medir([0, 1], [0, 10], {'metrica': metrica, 'desde': .4, 'hasta': .6}), esperado)
        self.assertEqual(medir([0, 1], [0, 10], {'metrica': 'valor_en', 'desde': .4, 'hasta': .6, 'en': .5}), 5)

    def test_no_extrapola_y_respeta_instantes_exactos(self):
        for objetivo, esperado in [(-1, None), (0, 0), (.5, 5), (1, 10), (2, None)]:
            self.assertEqual(medir([0, 1], [0, 10], {'metrica': 'valor_en', 'en': objetivo}), esperado)
        self.assertEqual(medir([0], [5], {'metrica': 'final'}), 5)

    def test_frecuencia_en_ventana_y_malla_irregular(self):
        eje = [0, .2, .8, 1.1, 1.2, 1.8, 2.1]
        valores = [0, 1, 0, 0, 1, 0, 0]
        self.assertAlmostEqual(medir(eje, valores, {'metrica': 'frecuencia', 'umbral': .5}), 1 / 1.05)
        self.assertIsNone(medir(eje, valores, {'metrica': 'frecuencia', 'umbral': .5, 'desde': .5}))

    def test_consulta_puntual_no_recorre_senal(self):
        class Contada(list):
            lecturas = 0
            def __getitem__(self, key):
                self.lecturas += 1
                return super().__getitem__(key)
            def __iter__(self):
                raise AssertionError('Una consulta puntual no debe recorrer todo el eje')
        eje = Contada(range(100000))
        self.assertEqual(medir(eje, range(100000), {'metrica': 'valor_en', 'en': 99980.5}), 99980.5)
        self.assertLess(eje.lecturas, 30)

    def resumen(self, eje, valores):
        return resumir({'eje': eje, 'vectores': {'v(a)': {'real': valores, 'imag': None}}, 'motor': 'prueba', 'log': []},
                       {'sondas': [{'id': 'a', 'vector': 'v(a)', 'unidad': 'V'}], 'medidas': [], 'eje_info': {}})

    def test_rechaza_senales_inconsistentes(self):
        for eje, valores in [([], []), ([0, 1], [1]), ([1, 0], [0, 1]), ([0, 0], [1, 2]), ([0, 1], [0, math.nan])]:
            with self.subTest(eje=eje), self.assertRaises(ValueError):
                self.resumen(eje, valores)

    def test_reduccion_preserva_picos_y_extremos(self):
        eje, valores = list(range(10000)), [0.] * 10000
        valores[401], valores[402] = 100, -100
        r = self.resumen(eje, valores)['series'][0]
        self.assertIn([401, 100], r['puntos'])
        self.assertIn([402, -100], r['puntos'])
        self.assertEqual(r['puntos'][0], [0, 0])
        self.assertEqual(r['puntos'][-1], [9999, 0])
        self.assertLessEqual(len(r['puntos']), 1502)
        self.assertEqual(len(self.resumen([0, 1, 2], [0, 5, 0])['series'][0]['puntos']), 3)

    def test_preparacion_fallida_no_deja_restos(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Servicio(Path(tmp))
            with self.assertRaises(ValueError):
                s.preparar({}, {'ejemplo': 'desconocido'})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_formas_json_invalidas_fallan_claramente(self):
        for c in (None, [], {'version': True}, {'version': 1, 'componentes': {'R1': None}},
                  {'version': 1, 'componentes': {'R1': {}}, 'modelos': 'archivo.lib'}):
            with self.subTest(c=c), self.assertRaises(ErrorSimulacion):
                validar_config(c)

    def test_historial_calcula_revision_una_vez_y_no_cachea_entre_consultas(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            s = Servicio(raiz / 'trabajos')
            proyecto = str(raiz)
            for i in range(5):
                id_ = f'{i:032x}'
                guardar_json(s.carpeta / id_ / 'meta.json', {'id': id_, 'proyecto_id': proyecto,
                             'revision': 'a', 'dependencias': {}, 'estado': 'completado'})
            with patch('halo.simulacion.servicio.revision', return_value='a') as rev:
                self.assertTrue(all(m['vigencia'] == 'vigente' for m in s.listar(proyecto)))
                self.assertEqual(rev.call_count, 1)
                rev.return_value = 'b'
                self.assertTrue(all(m['vigencia'] == 'obsoleto' for m in s.listar(proyecto)))
                self.assertEqual(rev.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
