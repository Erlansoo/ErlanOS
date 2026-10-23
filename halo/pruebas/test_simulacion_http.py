"""API HTTP real, con servidor y carpeta de resultados aislados; no abre ni modifica diseños."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from halo import servidor
from halo.simulacion.servicio import Servicio


class ApiSimulacion(unittest.TestCase):
    def test_ciclo_http_y_validacion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(servidor, 'SIMULACION', Servicio(Path(tmp))):
            http = ThreadingHTTPServer(('127.0.0.1', 0), servidor.Manejador)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(http.server_port)

            def peticion(ruta, cuerpo=None):
                data = json.dumps(cuerpo).encode() if cuerpo is not None else None
                with urlopen(Request(base + ruta, data, {'Content-Type': 'application/json', 'X-Halo-Request': '1'}), timeout=15) as r:
                    return json.load(r)
            try:
                self.assertIn('simulacion', peticion('/api/capacidades'))
                m = peticion('/api/simulacion', {'op': 'preparar', 'ejemplo': 'rc_ac'})
                peticion('/api/simulacion', {'op': 'ejecutar', 'id': m['id']})
                limite = time.monotonic() + 10
                while True:
                    r = peticion('/api/simulacion?id=' + m['id'])
                    if r['estado'] != 'ejecutando' or time.monotonic() > limite:
                        break
                    time.sleep(.05)
                self.assertEqual(r['estado'], 'completado', r.get('error'))
                self.assertEqual(r['resultado']['criterios'], 'cumple')
                resumen = peticion('/api/simulacion?id=' + m['id'] + '&resumen=1')
                self.assertEqual(resumen['estado'], 'completado')
                self.assertNotIn('resultado', resumen)
                self.assertIn(m['id'], [t['id'] for t in peticion('/api/simulacion')['trabajos']])
                with self.assertRaises(HTTPError) as e:
                    peticion('/api/simulacion', {'op': 'preparar', 'proyecto_id': 'inexistente', 'expected_revision': 'antigua'})
                self.assertEqual(e.exception.code, 409)
                with self.assertRaises(HTTPError) as e:
                    peticion('/api/simulacion?id=../../etc')
                self.assertEqual(e.exception.code, 400)
                with urlopen(base + '/') as page:
                    self.assertIn(b'/simulacion.js', page.read())
            finally:
                http.shutdown()
                http.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
