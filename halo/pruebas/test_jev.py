"""Jev sin red: forma de la petición, opción «no_se» y marca de duda."""
import io
import json
import unittest
from unittest import mock

from halo import jev


def _falso(respuesta: dict):
    """Sustituto de urlopen que guarda la petición y devuelve un JSON fijo."""
    pedidas = []

    def abrir(req, timeout=0):
        pedidas.append(req)
        return io.BytesIO(json.dumps(respuesta).encode())
    abrir.pedidas = pedidas
    return abrir


class Jev(unittest.TestCase):
    def setUp(self):
        self.entorno = mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": "clave-de-prueba"})
        self.entorno.start()

    def tearDown(self):
        self.entorno.stop()

    def test_peticion_con_clave_modelo_fijo_y_no_se(self):
        abrir = _falso({"model": "jev-1.13.0", "answers": {"q": {"type": "choice", "choice": "A",
                        "probabilities": {"A": 0.9, "B": 0.05, "no_se": 0.05}, "confidence": 0.9}},
                        "usage": {"input_tokens": 120, "output_tokens": 3}})
        r = jev.elegir("estado", "¿cuál?", {"A": "una", "B": "otra"}, abrir=abrir)
        req = abrir.pedidas[0]
        self.assertTrue(req.full_url.endswith("/v1/systemone"))
        self.assertEqual(req.get_header("Authorization"), "Bearer clave-de-prueba")
        cuerpo = json.loads(req.data)
        self.assertEqual(cuerpo["model"], jev.MODELO)
        self.assertNotEqual(cuerpo["model"], "jev-latest")
        self.assertIn("no_se", cuerpo["questions"]["q"]["criteria"])
        self.assertEqual(r["eleccion"], "A")
        self.assertFalse(r["dudosa"])
        self.assertEqual(r["tokens"], 120)

    def test_confianza_baja_o_no_se_es_dudosa(self):
        abrir = _falso({"model": "jev-1.13.0", "answers": {"q": {"type": "choice", "choice": "A",
                        "probabilities": {"A": 0.5, "B": 0.5}, "confidence": 0.2}}, "usage": {}})
        self.assertTrue(jev.elegir("e", "p", {"A": "a", "B": "b"}, abrir=abrir)["dudosa"])
        abrir = _falso({"model": "jev-1.13.0", "answers": {"q": {"type": "choice", "choice": "no_se",
                        "probabilities": {"no_se": 0.9}, "confidence": 0.9}}, "usage": {}})
        self.assertTrue(jev.elegir("e", "p", {"A": "a", "B": "b"}, abrir=abrir)["dudosa"])

    def test_si_no_y_escala(self):
        abrir = _falso({"model": "m", "answers": {"q": {"type": "noul", "noul": 0.96}}, "usage": {}})   # el sí/no no trae confianza
        r = jev.si_no("e", "¿?", abrir=abrir)
        self.assertTrue(r["si"]); self.assertFalse(r["dudosa"]); self.assertAlmostEqual(r["confianza"], 0.92)
        abrir = _falso({"model": "m", "answers": {"q": {"type": "noul", "noul": 0.55}}, "usage": {}})
        self.assertTrue(jev.si_no("e", "¿?", abrir=abrir)["dudosa"])          # 0,55 no es ni sí ni no
        abrir = _falso({"model": "m", "answers": {"q": {"type": "score", "score": 1.78, "confidence": 0.7}}, "usage": {}})
        r = jev.puntuar("e", "¿?", ["lista", "revisar", "bloqueada"], abrir=abrir)
        self.assertEqual(r["nivel"], "bloqueada")
        self.assertEqual(json.loads(abrir.pedidas[0].data)["questions"]["q"]["criteria"], ["lista", "revisar", "bloqueada"])

    def test_sin_clave_no_hay_jev(self):
        with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": ""}), mock.patch.object(jev, "clave", return_value=None):
            self.assertFalse(jev.disponible())
            with self.assertRaises(RuntimeError):
                jev.decidir("e", {})

    def test_cli(self):
        abrir = _falso({"model": "jev-1.13.0", "answers": {"q": {"type": "choice", "choice": "B",
                        "probabilities": {"A": 0.1, "B": 0.85, "no_se": 0.05}, "confidence": 0.8}}, "usage": {"input_tokens": 90}})
        with mock.patch.object(jev.urllib.request, "urlopen", abrir), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            codigo = jev.main(["estado", "-p", "¿cuál?", "-o", "A=una", "-o", "B=otra"])
        self.assertEqual(codigo, 0)
        self.assertIn("elige «B»", out.getvalue())


if __name__ == "__main__":
    unittest.main()
