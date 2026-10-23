"""Proyectos y pestañas: estado por proyecto, un servidor para varios, persistencia y avisos de los motores.

    python -m unittest halo.pruebas.test_pestanas
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo import proyectos, servidor  # noqa: E402
from halo.kicad import sesion  # noqa: E402

FUENTE = REPO / "halo" / "proyectos" / "parpadeo555"


def proyecto_de_prueba(raiz: Path, nombre: str) -> Path:
    carpeta = raiz / nombre
    carpeta.mkdir()
    for extension in (".kicad_sch", ".kicad_pro", ".kicad_pcb"):
        origen = FUENTE / f"parpadeo555{extension}"
        if origen.is_file():
            shutil.copy2(origen, carpeta / f"{nombre}{extension}")
    return carpeta


class Pestanas(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)
        self.estado = self.raiz / "estado"
        self.estado.mkdir()
        self.a = proyecto_de_prueba(self.raiz, "alfa")
        self.b = proyecto_de_prueba(self.raiz, "beta")
        self.centro = servidor.Centro(self.estado)
        self.addCleanup(self.centro.observador.cerrar)

    def test_abrir_cerrar_activar_y_persistir(self):
        pa = self.centro.abrir(self.a)
        pb = self.centro.abrir(self.b, activar=False)
        self.assertEqual([p["id"] for p in self.centro.pestanas()], [pa.id, pb.id])
        self.assertEqual(self.centro.activo, pa.id)
        self.assertTrue((self.a / ".halo" / "actual.json").is_file(), "abrir deja el estado inicial en el proyecto")
        self.centro._vuelta()
        self.assertEqual(pa.actual["proyecto"], "Parpadeador LED NE555")
        self.assertIn("esquematico", pa.modelos)
        self.centro.activar(pb.id)
        self.assertEqual(self.centro.activo, pb.id)
        self.centro.reordenar([pb.id, pa.id])
        self.assertEqual([p["id"] for p in self.centro.pestanas()], [pb.id, pa.id])
        otro = servidor.Centro(self.estado)
        self.addCleanup(otro.observador.cerrar)
        otro.restaurar()
        self.assertEqual([p["id"] for p in otro.pestanas()], [pb.id, pa.id], "las pestañas vuelven en su orden")
        self.assertEqual(otro.activo, pb.id)
        self.centro.cerrar(pb.id)
        self.assertEqual(self.centro.activo, pa.id)
        with self.assertRaises(servidor.ErrorPestana):
            self.centro.abrir(self.raiz / "no-existe")
        with self.assertRaises(servidor.ErrorPestana):
            self.centro.abrir(self.estado)   # una carpeta sin KiCad no es un proyecto

    def test_cada_pestana_tiene_su_estado(self):
        pa, pb = self.centro.abrir(self.a), self.centro.abrir(self.b)
        self.centro._vuelta()
        ia, ib = self.centro.instantanea(pa.id), self.centro.instantanea(pb.id)
        self.assertEqual(ia["proyecto"], pa.id)
        self.assertEqual(ib["proyecto"], pb.id)
        self.assertNotEqual(ia["actual"]["archivos"]["esquematico"], ib["actual"]["archivos"]["esquematico"])
        self.assertEqual(len(ia["pestanas"]), 2)
        self.assertIsNone(self.centro.instantanea("no-abierta")["proyecto"])

    def test_un_motor_abre_su_pestana(self):
        with patch.object(sesion, "ESTADO", self.estado):
            s = sesion.Sesion("Prueba", self.a / "alfa.kicad_sch")
            s.halo("actuando", "trabajando")
        self.assertTrue((self.a / ".halo" / "actual.json").is_file())
        self.assertTrue(list((self.estado / "abrir").glob("*.json")), "el motor deja el aviso")
        self.centro._vuelta()
        self.assertEqual([p["carpeta"] for p in self.centro.pestanas()], [str(self.a.resolve())])
        self.assertFalse(list((self.estado / "abrir").glob("*.json")), "el aviso se consume")
        self.assertEqual(self.centro.pestanas()[0]["estado"], "actuando")

    def test_api_http(self):
        with patch.object(servidor, "CENTRO", self.centro):
            http = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manejador)
            hilo = threading.Thread(target=http.serve_forever, daemon=True)
            hilo.start()
            base = f"http://127.0.0.1:{http.server_port}"

            def pedir(ruta, cuerpo=None, cabeceras=None):
                datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
                req = Request(base + ruta, datos, {"Content-Type": "application/json", "X-Halo-Request": "1", **(cabeceras or {})})
                with urlopen(req, timeout=15) as r:
                    return json.load(r)
            try:
                self.assertEqual(pedir("/api/pestanas")["pestanas"], [])
                r = pedir("/api/pestanas", {"op": "abrir", "carpeta": str(self.a)})
                self.assertEqual(r["activo"], r["proyecto"])
                r = pedir("/api/pestanas", {"op": "abrir", "carpeta": str(self.b)})
                self.assertEqual(len(r["pestanas"]), 2)
                self.centro._vuelta()
                estado = pedir("/api/estado?proyecto=" + str(self.a.resolve()).replace("\\", "%5C"))
                self.assertEqual(estado["actual"]["proyecto_id"], str(self.a.resolve()))
                self.assertIn("esquematico", estado["modelos"])
                with self.assertRaises(HTTPError) as error:
                    pedir("/api/pestanas", {"op": "abrir", "carpeta": str(self.raiz / "nada")})
                self.assertEqual(error.exception.code, 409)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/pestanas", b"{}", {"Content-Type": "application/json"}))
                self.assertEqual(error.exception.code, 403, "sin la cabecera propia no se abre nada")
                r = pedir("/api/pestanas", {"op": "cerrar", "proyecto_id": str(self.b.resolve())})
                self.assertEqual(len(r["pestanas"]), 1)
            finally:
                http.shutdown()
                http.server_close()
                hilo.join()

    def test_nombre_y_archivos(self):
        self.assertEqual(proyectos.nombre_de(self.a), "Parpadeador LED NE555")
        self.assertEqual(set(proyectos.archivos_de(self.a)), {"esquematico", "pcb"})
        self.assertFalse(proyectos.es_proyecto(self.estado))
        inicial = proyectos.estado_inicial(self.a)
        self.assertEqual(inicial["etapa"], "esquematico")
        self.assertEqual(inicial["halo"]["estado"], "reposo")


if __name__ == "__main__":
    unittest.main()
