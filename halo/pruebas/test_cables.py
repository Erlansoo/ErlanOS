"""Cables a mano en el esquemático: jalar un tramo, trazar uno nuevo y borrarlo, como en KiCad."""
import tempfile
import unittest
from pathlib import Path

from halo.kicad.esquematico import Esquematico, _en_interior


def _hoja(carpeta):
    e = Esquematico(carpeta, "cables", titulo="Cables a mano")
    e.colocar("Device:R", "R1", "1k", 50.8, 50.8)
    e.colocar("Device:R", "R2", "2k", 76.2, 50.8)
    e.colocar("Device:C", "C1", "100n", 101.6, 50.8)
    return e


class Cables(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.carpeta = Path(self.tmp.name)
        self.e = _hoja(self.carpeta)

    def tearDown(self):
        self.tmp.cleanup()

    def _cables(self, red):
        return [w for w in self.e.cables if w["red"] == red]

    def _extremos(self, red):
        return {p for w in self._cables(red) for p in (w["a"], w["b"])}

    def test_mover_cable_estira_vecinos_y_deja_codo_en_pines(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))              # pin a pin con codos automáticos
        pines = {e.pin("R1", 2), e.pin("R2", 2)}
        horizontales = [w for w in self._cables("A") if w["a"][1] == w["b"][1]]
        self.assertTrue(horizontales)
        w = horizontales[0]
        antes, pasos = len(e.cables), len(e._historial)
        e.mover_cable(w["uuid"], 0, 2.54, autor="usuario")
        self.assertEqual(w["a"][1], w["b"][1])          # sigue horizontal, 2.54 más abajo
        self.assertTrue(pines <= self._extremos("A"))   # los pines siguen conectados
        e._reconstruir_redes()                          # la conectividad se rehace por geometría (sin etiqueta, la red se renombra)
        self.assertEqual(e.pin_red[("R1", "2")], e.pin_red[("R2", "2")])
        self.assertGreaterEqual(len(e.cables), antes)   # nada se perdió; a lo sumo aparecieron codos
        self.assertEqual(len(e._historial), pasos + 1)
        y = w["a"][1]
        e.deshacer()
        self.assertEqual(next(x for x in e.cables if x["uuid"] == w["uuid"])["a"][1], y - 2.54)

    def test_mover_cable_rechaza_cortocircuito(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        e.red("B", ("R2", 1), ("C1", 1))
        w = next(x for x in self._cables("B") if x["a"][1] == x["b"][1])
        # Empujarlo hasta el pin R1.2 (red A) sería un corto: se rechaza y no queda nada en el historial.
        salto = e.pin("R1", 2)[1] - w["a"][1]
        e.cables.append({"a": (w["a"][0] - 50, w["a"][1]), "b": w["a"], "red": "B", "uuid": "x"})  # tramo largo hacia R1
        largo, pasos = e.cables[-1], len(e._historial)
        with self.assertRaises(ValueError):
            e.mover_cable(largo["uuid"], 0, salto)
        self.assertEqual(len(e._historial), pasos)

    def test_trazar_cable_toma_la_red_de_lo_que_toca(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        a = e.pin("C1", 1)
        w = next(x for x in self._cables("A") if x["a"][1] == x["b"][1])
        medio = ((w["a"][0] + w["b"][0]) / 2, w["a"][1])
        medio = (round(medio[0] / 1.27) * 1.27, medio[1])
        red = e.trazar_cable([a, (a[0], medio[1]), medio], autor="usuario")
        self.assertEqual(red, "A")
        self.assertEqual(e.pin_red[("C1", "1")], "A")
        self.assertIn(medio, [p for x in e.cables for p in (x["a"], x["b"])])
        self.assertIn(medio, e.uniones())                 # tocó el interior de un tramo: hay unión

    def test_trazar_cable_sin_tocar_nada_estrena_red_y_rechaza_unir_dos(self):
        e = self.e
        red = e.trazar_cable([(10.16, 10.16), (20.32, 10.16), (20.32, 20.32)])
        self.assertEqual(red, "N1")
        self.assertEqual(len(self._cables("N1")), 2)
        e.red("A", ("R1", 2), ("R2", 2))
        e.red("B", ("R2", 1), ("C1", 1))
        with self.assertRaises(ValueError):
            e.trazar_cable([e.pin("R1", 2), e.pin("C1", 1)])
        with self.assertRaises(ValueError):
            e.trazar_cable([(10.16, 10.16), (20.32, 20.32)])   # diagonal

    def test_borrar_cable_y_deshacer(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        w = self._cables("A")[0]
        n = len(e.cables)
        e.borrar_cable(w["uuid"], autor="usuario")
        self.assertEqual(len(e.cables), n - 1)
        e.deshacer()
        self.assertEqual(len(e.cables), n)

    def test_reabrir_conserva_lo_trazado(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        a = e.pin("C1", 1)
        w = next(x for x in self._cables("A") if x["a"][1] == x["b"][1])
        medio = (round((w["a"][0] + w["b"][0]) / 2 / 1.27) * 1.27, w["a"][1])
        e.trazar_cable([a, (a[0], medio[1]), medio])
        e.guardar()
        otro = Esquematico.abrir(self.carpeta / "cables.kicad_sch")
        self.assertEqual(otro.pin_red[("C1", "1")], otro.pin_red[("R1", "2")])
        self.assertEqual(len(otro.cables), len(e.cables))


if __name__ == "__main__":
    unittest.main()


class Etiquetas(unittest.TestCase):
    def test_rotar_etiqueta_gira_sobre_su_punto(self):
        tmp = tempfile.TemporaryDirectory(); carpeta = Path(tmp.name)
        e = _hoja(carpeta)
        e.etiqueta("SENAL", ("R1", 2))
        et = e.etiquetas[0]
        punto, ang, cables = et["at"], et["ang"], len(e.cables)
        e.rotar_etiqueta(et["uuid"], 90, autor="usuario")
        self.assertEqual(et["at"], punto)                  # el punto de conexión no se mueve
        self.assertEqual(et["ang"], (ang + 90) % 360)
        self.assertEqual(len(e.cables), cables)            # el cable queda como estaba
        e.guardar()
        otro = Esquematico.abrir(carpeta / "cables.kicad_sch")
        self.assertEqual(otro.etiquetas[0]["ang"], (ang + 90) % 360)
        self.assertEqual(otro.pin_red[("R1", "2")], "SENAL")
        e.deshacer()
        self.assertEqual(e.etiquetas[0]["ang"], ang)   # deshacer repone la instantánea
        tmp.cleanup()


class Orientacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.carpeta = Path(self.tmp.name)
        self.e = _hoja(self.carpeta)

    def tearDown(self):
        self.tmp.cleanup()

    def _particion(self):
        e = self.e; e._reconstruir_redes(); g = {}
        for pin, red in e.pin_red.items(): g.setdefault(red, set()).add(pin)
        return {frozenset(x) for x in g.values() if len(x) > 1}

    def test_rotar_con_un_solo_pin_conectado_pivota_en_ese_pin(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        pivote = e.pin("R1", 2)
        base = self._particion()
        for rot in (90, 180, 270, 0):
            e.rotar("R1", 90, autor="usuario")
            s = e._buscar("R1")
            self.assertEqual(s.rot, rot)
            self.assertEqual(e.pin("R1", 2), pivote)       # el pin conectado no se mueve
            self.assertEqual(self._particion(), base)      # y nada se desconecta

    def test_rotar_con_dos_pines_conectados_gira_sobre_el_centro(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2)); e.red("B", ("R1", 1), ("C1", 1))
        centro = e._buscar("R1").at; base = self._particion()
        e.rotar("R1", 90)
        self.assertEqual(e._buscar("R1").at, centro)
        self.assertEqual(self._particion(), base)

    def test_espejar_como_kicad(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2)); e.red("B", ("R1", 1), ("C1", 1))
        base = self._particion(); s = e._buscar("R1")
        e.espejar("R1", "y", autor="usuario"); self.assertEqual(s.espejo, "y"); self.assertEqual(self._particion(), base)
        e.espejar("R1", "y"); self.assertIsNone(s.espejo)                       # dos veces: como estaba
        e.espejar("R1", "y"); e.espejar("R1", "x")
        self.assertIsNone(s.espejo); self.assertEqual(s.rot, 180)               # x tras y = girar 180°, como guarda KiCad
        self.assertEqual(self._particion(), base)
        with self.assertRaises(ValueError):
            e.espejar("R1", "z")
        e.guardar()
        otro = Esquematico.abrir(self.carpeta / "cables.kicad_sch")
        self.assertEqual(otro._buscar("R1").rot, 180)


class EtiquetasYRedesSinNombre(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.carpeta = Path(self.tmp.name)
        self.e = _hoja(self.carpeta)

    def tearDown(self):
        self.tmp.cleanup()

    def test_etiqueta_en_medio_del_tramo_viaja_con_el(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        w = next(x for x in e.cables if x["red"] == "A" and x["a"][1] == x["b"][1])
        medio = (round((w["a"][0] + w["b"][0]) / 2 / 1.27) * 1.27, w["a"][1])
        e.etiquetas.append({"texto": "MEDIO", "at": medio, "ang": 0, "red": "A", "uuid": "et-medio", "tipo": "local", "forma": "passive"})
        e.mover_cable(w["uuid"], 0, 2.54, autor="usuario")
        et = next(x for x in e.etiquetas if x["texto"] == "MEDIO")
        self.assertEqual(et["at"], (medio[0], medio[1] + 2.54))
        self.assertTrue(any(_en_interior(et["at"], x["a"], x["b"]) or et["at"] in (x["a"], x["b"]) for x in e.cables))

    def test_trazar_funde_una_red_sin_nombre_con_la_etiqueta(self):
        e = self.e
        e.red("A", ("R1", 2), ("R2", 2))
        e.trazar_cable([e.pin("C1", 1), (e.pin("C1", 1)[0] + 5.08, e.pin("C1", 1)[1])])   # C1.1 en una red sin nombre
        anonima = e.pin_red[("C1", "1")]
        self.assertTrue(e._anonima(anonima))
        fin = (e.pin("C1", 1)[0] + 5.08, e.pin("C1", 1)[1])
        w = next(x for x in e.cables if x["red"] == "A" and x["a"][1] == x["b"][1])
        red = e.trazar_cable([fin, (fin[0], w["a"][1] + 5.08), (w["a"][0], w["a"][1] + 5.08), w["a"]])
        self.assertEqual(red, "A")
        self.assertEqual(e.pin_red[("C1", "1")], "A")
        self.assertFalse(any(x["red"] == anonima for x in e.cables))
