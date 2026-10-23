"""El servidor no sondea: el sistema avisa de los cambios y los servicios avisan a la interfaz.

    python -m unittest halo.pruebas.test_vigia
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo.vigia import Observador  # noqa: E402


class Vigia(unittest.TestCase):
    def test_aviso_sin_sondeo(self):
        if not Observador(lambda: None).activo:
            self.skipTest('sólo hay avisos del sistema en Windows')
        with tempfile.TemporaryDirectory() as d:
            senal = threading.Event()
            observador = Observador(senal.set)
            observador.observar([d])
            self.assertEqual(len(observador._vigilias), 1)
            self.assertFalse(senal.wait(0.3), 'sin cambios no debe despertar')
            (Path(d) / 'sub').mkdir()
            (Path(d) / 'sub' / 'a.txt').write_text('x', encoding='utf-8')
            self.assertTrue(senal.wait(2), 'un cambio en una subcarpeta despierta')
            observador.observar([])   # soltar la carpeta: el hilo termina y se puede borrar
            time.sleep(0.2)
            self.assertEqual(observador._vigilias, {})
            self.assertGreaterEqual(observador.espera, 0.5)

    def test_carpetas_inexistentes_se_ignoran(self):
        observador = Observador(lambda: None)
        observador.observar([Path(tempfile.gettempdir()) / 'halo-no-existe-9f3a'])
        self.assertEqual(observador._vigilias, {})
        observador.cerrar()

    def test_latidos_del_centro(self):
        from halo.servidor import Centro
        centro = Centro()
        version = centro.version
        centro.notificar('simulacion')
        centro.notificar('simulacion')
        centro.notificar('agentes')
        datos = centro.instantanea(False)
        self.assertEqual(datos['latidos'], {'simulacion': 2, 'agentes': 1})
        self.assertGreater(centro.version, version)
        centro.observador.cerrar()


if __name__ == '__main__':
    unittest.main()
