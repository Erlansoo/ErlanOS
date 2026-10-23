"""Evidencia, identidad y API de fichas; no modifica proyectos KiCad reales."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from halo import bloques, servidor
from halo.kicad.proyecto import revision


class Fichas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name).resolve()
        (self.raiz / '.halo').mkdir()
        # An opaque design marker: tests never manufacture or parse a KiCad graph.
        self.actual = {'proyecto_id': str(self.raiz), 'revision': revision(self.raiz),
                       'archivos': {'esquematico': str(self.raiz / 'prueba.kicad_sch')}}
        self.modelo = {'archivo': self.actual['archivos']['esquematico'], 'bloques': [], 'textos': [],
                       'simbolos': [{'ref': 'R1', 'valor': '10k', 'lib_id': 'Device:R'}]}
        self.ficha = {'rectangulo_uuid': 'halo-filtro', 'titulo': 'Filtro', 'a': [1, 2], 'b': [50, 40],
                      'referencias': ['R1'], 'funcion': 'Filtrar la señal', 'pruebas': []}
        self.guardar()

    def guardar(self):
        (self.raiz / '.halo/bloques.json').write_text(json.dumps({
            'version': 1, 'revision': self.actual['revision'], 'bloques': [self.ficha]}), encoding='utf-8')
        self.modelo['bloques'] = []
        bloques.incorporar(self.modelo)

    def leer(self, simulacion=None):
        return bloques.consultar(self.actual, self.modelo, 'halo-filtro', simulacion)['ficha']

    def test_virtual_y_componentes(self):
        self.assertTrue(self.modelo['bloques'][0]['virtual'])
        bloques.incorporar(self.modelo)
        self.assertEqual(len(self.modelo['bloques']), 1)
        self.assertEqual(self.leer()['componentes'][0]['valor'], '10k')
        self.assertEqual(self.leer()['vigencia'], 'vigente')
        self.ficha['referencias'].append('R99'); self.guardar()
        self.assertEqual(self.leer()['referencias_ausentes'], ['R99'])

    def test_evidencia_debe_existir_coincidir_y_ser_vigente(self):
        archivo = self.raiz / '.halo/ensayo.txt'; archivo.write_text('medición')
        p = {'ruta': '.halo/ensayo.txt', 'sha256': hashlib.sha256(archivo.read_bytes()).hexdigest(),
             'revision': self.actual['revision'], 'estado': 'aprobado'}
        self.ficha['pruebas'] = [p]; self.guardar()
        self.assertEqual(self.leer()['pruebas'][0]['verificacion'], 'vigente')
        p['revision'] = 'antigua'; self.guardar()
        self.assertEqual(self.leer()['pruebas'][0]['verificacion'], 'desactualizada')
        archivo.write_text('otro resultado')
        self.assertEqual(self.leer()['pruebas'][0]['verificacion'], 'evidencia_modificada')
        p['ruta'] = '../fuera.txt'; self.guardar()
        self.assertEqual(self.leer()['pruebas'][0]['verificacion'], 'no_disponible')

    def test_no_convierte_declaracion_en_prueba(self):
        self.ficha['pruebas'] = [{'estado': 'aprobado', 'descripcion': 'Sin evidencia'}]; self.guardar()
        self.assertEqual(self.leer()['pruebas'][0]['verificacion'], 'pendiente')

    def test_documento_roto_no_oculta_el_esquematico(self):
        (self.raiz / '.halo/bloques.json').write_text('{')
        self.assertIn('error_bloques', bloques.incorporar(self.modelo))
        self.assertEqual(self.modelo['simbolos'][0]['ref'], 'R1')
        with self.assertRaises(ValueError): self.leer()

    def test_simulacion_no_puede_ser_demo_ni_otro_proyecto(self):
        self.ficha['pruebas'] = [{'simulacion_id': 'a' * 32}]; self.guardar()
        meta = {'estado': 'completado', 'vigencia': 'vigente', 'proyecto_id': 'otro'}
        self.assertEqual(self.leer(lambda _: meta)['pruebas'][0]['verificacion'], 'no_corresponde')
        meta.update(proyecto_id=str(self.raiz), ejemplo='rc')
        self.assertEqual(self.leer(lambda _: meta)['pruebas'][0]['verificacion'], 'no_corresponde')
        meta.update(ejemplo=None, resultado={'criterios': 'no_cumple'})
        prueba = self.leer(lambda _: meta)['pruebas'][0]
        self.assertEqual(prueba['verificacion'], 'vigente')
        self.assertEqual(prueba['resultado'], 'no_cumple')

    def test_bloque_ajeno_fuentes_y_revision(self):
        with self.assertRaises(bloques.ErrorBloque): bloques.consultar(self.actual, self.modelo, '../../otro')
        self.ficha['fuentes'] = [{'titulo': 'Mala', 'url': 'javascript:alert(1)'}]; self.guardar()
        self.assertNotIn('url', self.leer()['fuentes'][0])
        self.actual['revision'] = 'vieja'
        with self.assertRaises(bloques.ErrorBloque): self.leer()

    def test_http_proyecto_activo(self):
        centro = servidor.Centro(); centro.actual = self.actual; centro.modelos = {'esquematico': self.modelo}
        with patch.object(servidor, 'CENTRO', centro):
            http = ThreadingHTTPServer(('127.0.0.1', 0), servidor.Manejador)
            t = threading.Thread(target=http.serve_forever, daemon=True); t.start()
            try:
                url = f'http://127.0.0.1:{http.server_port}/api/bloques'
                with urlopen(url) as r: self.assertEqual(len(json.load(r)['bloques']), 1)
                with urlopen(url + '?uuid=halo-filtro') as r: self.assertEqual(json.load(r)['ficha']['titulo'], 'Filtro')
                with self.assertRaises(HTTPError) as error: urlopen(url + '?proyecto_id=otro')
                self.assertEqual(error.exception.code, 409)
            finally:
                http.shutdown(); http.server_close(); t.join()


if __name__ == '__main__': unittest.main()
