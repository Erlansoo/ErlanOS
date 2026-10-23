"""Deshacer sólo deshace lo tuyo: si la IA (o KiCad) tocó el archivo después, la pila se reinicia."""
import tempfile
import unittest
from pathlib import Path

from halo import editor


class Deshacer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.carpeta = Path(self.tmp.name)
        (self.carpeta / ".halo").mkdir()
        self.ruta = self.carpeta / "x.kicad_sch"
        self.historial = self.carpeta / ".halo" / "historial"
        self.actual = {"proyecto_id": "p", "carpeta": str(self.carpeta)}
        self.ruta.write_text("v0", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _cambio_tuyo(self, contenido):
        copia = editor._copia_previa(self.ruta, self.historial)
        self.ruta.write_text(contenido, encoding="utf-8")
        editor._sellar(copia, self.ruta)

    def test_deshace_lo_tuyo_en_orden(self):
        self._cambio_tuyo("v1"); self._cambio_tuyo("v2")
        editor._deshacer(self.actual, self.ruta, self.historial, "x")
        self.assertEqual(self.ruta.read_text(encoding="utf-8"), "v1")
        editor._deshacer(self.actual, self.ruta, self.historial, "x")
        self.assertEqual(self.ruta.read_text(encoding="utf-8"), "v0")
        with self.assertRaises(editor.ErrorEdicion):
            editor._deshacer(self.actual, self.ruta, self.historial, "x")

    def test_si_la_ia_toco_el_archivo_la_pila_se_reinicia(self):
        self._cambio_tuyo("v1")
        self.ruta.write_text("v1 + trabajo de la IA", encoding="utf-8")    # la IA guarda sin pasar por el editor
        with self.assertRaises(editor.ErrorEdicion) as ctx:
            editor._deshacer(self.actual, self.ruta, self.historial, "x")
        self.assertIn("se reinició", str(ctx.exception))
        self.assertEqual(self.ruta.read_text(encoding="utf-8"), "v1 + trabajo de la IA")   # lo de la IA no se pierde
        self.assertEqual(list(self.historial.glob("*")), [])                              # pila vacía
        self._cambio_tuyo("v2")                                                          # y desde aquí vuelve a valer
        editor._deshacer(self.actual, self.ruta, self.historial, "x")
        self.assertEqual(self.ruta.read_text(encoding="utf-8"), "v1 + trabajo de la IA")


if __name__ == "__main__":
    unittest.main()
