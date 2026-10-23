"""Documentación viva: Markdown propio, memoria e informe generados, secciones desactualizadas, LaTeX, guía.

    python -m unittest halo.pruebas.test_documentos
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from halo.documentos import markdown, servicio  # noqa: E402

EJEMPLO = REPO / "halo" / "proyectos" / "parpadeo555"


class Markdown(unittest.TestCase):
    def test_bloques_y_formato(self):
        html = markdown.render("# Título uno\n\nPárrafo con **negrita**, *cursiva* y `código`.\n\n"
                               "- a\n- b\n  - b.1\n1. uno\n2. dos\n\n> cita\n\n```python\nx = 1 < 2\n```\n\n"
                               "| a | b |\n|---|--:|\n| 1 | 2 |\n\n---\n[enlace](https://kicad.org) ![fig](figuras/x.svg)")
        self.assertIn('<h1 id="titulo-uno">Título uno</h1>', html)
        self.assertIn("<strong>negrita</strong>", html)
        self.assertIn("<em>cursiva</em>", html)
        self.assertIn("<code>código</code>", html)
        self.assertIn("<ul><li>a</li><li>b<ul><li>b.1</li></ul></li></ul>", html)
        self.assertIn("<ol><li>uno</li><li>dos</li></ol>", html)
        self.assertIn("<blockquote><p>cita</p></blockquote>", html)
        self.assertIn('<pre><code class="lenguaje-python">x = 1 &lt; 2</code></pre>', html)
        self.assertIn('<th style="text-align:right">b</th>', html)
        self.assertIn("<hr>", html)
        self.assertIn('<a href="https://kicad.org" target="_blank" rel="noopener">enlace</a>', html)
        self.assertIn('<img src="figuras/x.svg" alt="fig">', html)

    def test_sin_html_crudo_ni_enlaces_peligrosos(self):
        html = markdown.render("<script>alert(1)</script> texto [x](javascript:alert(1)) <!-- halo:seccion pcb 1 -->\n\n<b>no</b>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("javascript:", html.replace("[x](javascript:alert(1))", ""))
        self.assertNotIn("<b>", html)
        self.assertNotIn("halo:seccion", html)

    def test_resolver_imagenes(self):
        html = markdown.render("![a](figuras/a.png)", resolver=lambda r: "/api/x?ruta=" + r)
        self.assertIn('src="/api/x?ruta=figuras/a.png"', html)


@unittest.skipUnless(EJEMPLO.is_dir(), "necesita el proyecto de ejemplo parpadeo555")
class Documentos(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.carpeta = Path(self.temp.name) / "parpadeo555"
        self.carpeta.mkdir()
        for p in EJEMPLO.iterdir():
            if p.suffix in (".kicad_sch", ".kicad_pcb", ".kicad_pro"):
                shutil.copy2(p, self.carpeta / p.name)
        (self.carpeta / ".halo").mkdir()
        for nombre in ("verificacion.json", "placa.json", "verificacion-pcb.json", "bloques.json"):
            origen = EJEMPLO / ".halo" / nombre
            if origen.is_file():
                shutil.copy2(origen, self.carpeta / ".halo" / nombre)

    def test_memoria_secciones_y_desactualizacion(self):
        r = servicio.crear(self.carpeta, "memoria")
        self.assertEqual(r["nombre"], "memoria.md")
        doc = servicio.leer(self.carpeta, "memoria.md")
        self.assertEqual([s["fuente"] for s in doc["secciones"]], ["esquematico", "simulacion", "pcb", "fabricacion"])
        self.assertTrue(all(s["estado"] == "vigente" for s in doc["secciones"]))
        self.assertIn("| U1 |", doc["texto"])
        self.assertIn("Temporizador astable NE555", doc["texto"])
        self.assertIn("<table>", doc["html"])
        self.assertIn('src="@figura/figuras/esquematico.svg"', doc["html"])
        self.assertEqual(doc["html"].count('<section class="doc-seccion vigente"'), 4)
        self.assertNotIn("halo:seccion", doc["html"])
        lista = servicio.listar(self.carpeta)
        self.assertEqual(lista["documentos"][0]["desactualizadas"], 0)
        # el usuario escribe fuera de las marcas: se conserva al actualizar
        texto = doc["texto"].replace("_Pendiente._", "Funciona a 1 Hz, medido en el laboratorio.")
        servicio.guardar(self.carpeta, "memoria.md", texto, doc["firma"])
        with self.assertRaises(servicio.ErrorDocumento):
            servicio.guardar(self.carpeta, "memoria.md", "otro", doc["firma"])   # firma vieja
        # cambia el esquemático → la sección del esquemático queda desactualizada; las otras no
        sch = next(self.carpeta.glob("*.kicad_sch"))
        sch.write_text(sch.read_text(encoding="utf-8").replace('(title "Parpadeador LED NE555")', '(title "Parpadeador LED NE555 v2")'), encoding="utf-8")
        doc = servicio.leer(self.carpeta, "memoria.md")
        estados = {s["fuente"]: s["estado"] for s in doc["secciones"]}
        self.assertEqual(estados, {"esquematico": "desactualizada", "simulacion": "vigente", "pcb": "vigente", "fabricacion": "vigente"})
        self.assertIn('<section class="doc-seccion desactualizada" data-fuente="esquematico">', doc["html"])
        self.assertIn("pulsa «Actualizar secciones»", doc["html"])
        self.assertEqual(servicio.listar(self.carpeta)["guia"]["etapas"][0]["estado"], "en curso")
        r = servicio.actualizar(self.carpeta, "memoria.md")
        self.assertTrue(r["cambio"])
        self.assertTrue(all(s["estado"] == "vigente" for s in r["secciones"]))
        nuevo = servicio.leer(self.carpeta, "memoria.md")["texto"]
        self.assertIn("Funciona a 1 Hz", nuevo, "lo escrito fuera de las marcas sobrevive")
        self.assertIn("# Parpadeador LED NE555", nuevo)
        self.assertEqual(nuevo.count("halo:seccion esquematico"), 1)
        with self.assertRaises(servicio.ErrorDocumento):
            servicio.crear(self.carpeta, "memoria")   # ya existe

    def test_guia_propone_siguiente_paso(self):
        g = servicio.guia(self.carpeta)
        self.assertEqual([e["etapa"] for e in g["etapas"]], ["documentacion", "esquematico", "simulacion", "pcb", "fabricacion", "3d"])
        self.assertEqual(g["etapas"][0]["estado"], "pendiente")
        self.assertTrue(g["siguiente"].startswith("Documentación:"))
        self.assertEqual(g["etapas"][1]["estado"], "lista", g["etapas"][1])
        servicio.crear(self.carpeta, "memoria")
        g = servicio.guia(self.carpeta)
        self.assertEqual(g["etapas"][0]["estado"], "lista")
        self.assertTrue(g["siguiente"].startswith("Simulación:"), g["siguiente"])
        self.assertIn("Siguiente paso sugerido", servicio.resumen_guia(self.carpeta))

    def test_nombres_y_rutas_seguras(self):
        with self.assertRaises(servicio.ErrorDocumento):
            servicio.leer(self.carpeta, "../parpadeo555.kicad_sch")
        with self.assertRaises(servicio.ErrorDocumento):
            servicio.archivo(self.carpeta, "../parpadeo555.kicad_pro")
        with self.assertRaises(servicio.ErrorDocumento):
            servicio.guardar(self.carpeta, "x.kicad_sch", "(kicad_sch)")

    @unittest.skipUnless(servicio.pdflatex() and servicio.kicad_cli(), "necesita MiKTeX y kicad-cli")
    def test_informe_latex_compila(self):
        servicio.crear(self.carpeta, "informe")
        doc = servicio.leer(self.carpeta, "informe.tex")
        self.assertEqual(len(doc["secciones"]), 4)
        self.assertIn("\\includegraphics[width=\\textwidth]{figuras/esquematico.pdf}", doc["texto"])
        self.assertTrue((self.carpeta / "docs" / "figuras" / "esquematico.pdf").is_file())
        self.assertTrue((self.carpeta / "docs" / "figuras" / "placa.svg").is_file())
        r = servicio.compilar(self.carpeta, "informe.tex")
        self.assertEqual(r["pdf"], "informe.pdf")
        pdf = self.carpeta / "docs" / "informe.pdf"
        self.assertTrue(pdf.is_file() and pdf.stat().st_size > 10000)
        self.assertTrue(servicio.leer(self.carpeta, "informe.tex")["pdf_vigente"])
        listado = {d["nombre"]: d for d in servicio.listar(self.carpeta)["documentos"]}
        self.assertTrue(listado["informe.tex"]["pdf_vigente"])
        self.assertEqual(listado["informe.pdf"]["tipo"], "pdf")

    @unittest.skipUnless(servicio.pandoc(), "necesita pandoc")
    def test_convertir_con_pandoc(self):
        servicio.crear(self.carpeta, "memoria")
        r = servicio.convertir(self.carpeta, "memoria.md", "docx")
        self.assertEqual(r["archivo"], "memoria.docx")
        self.assertTrue((self.carpeta / "docs" / "memoria.docx").stat().st_size > 5000)
        self.assertEqual({d["nombre"]: d["tipo"] for d in servicio.listar(self.carpeta)["documentos"]}["memoria.docx"], "docx")

    def test_api_http(self):
        import threading
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from halo import servidor
        centro = servidor.Centro(Path(self.temp.name) / "estado")
        self.addCleanup(centro.observador.cerrar)
        centro.abrir(self.carpeta)
        with patch.object(servidor, "CENTRO", centro):
            http = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manejador)
            hilo = threading.Thread(target=http.serve_forever, daemon=True)
            hilo.start()
            base = f"http://127.0.0.1:{http.server_port}"
            pid = str(self.carpeta.resolve())
            q = "?proyecto=" + pid.replace("\\", "%5C").replace(":", "%3A")

            def pedir(ruta, cuerpo=None, crudo=False):
                datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
                req = Request(base + ruta, datos, {"Content-Type": "application/json", "X-Halo-Request": "1"})
                with urlopen(req, timeout=60) as r:
                    return r.read() if crudo else json.load(r)
            try:
                self.assertEqual(pedir("/api/documentos" + q)["documentos"], [])
                r = pedir("/api/documentos", {"op": "crear", "plantilla": "memoria", "proyecto_id": pid})
                self.assertEqual(r["nombre"], "memoria.md")
                doc = pedir("/api/documento" + q + "&nombre=memoria.md")
                self.assertIn('src="/api/documento/archivo?proyecto=', doc["html"])
                self.assertIn("ruta=figuras/esquematico.svg", doc["html"])
                if servicio.kicad_cli():
                    svg = pedir("/api/documento/archivo" + q + "&ruta=figuras/esquematico.svg", crudo=True)
                    self.assertIn(b"<svg", svg)
                with self.assertRaises(HTTPError) as ctx:
                    pedir("/api/documento/archivo" + q + "&ruta=../parpadeo555.kicad_pro")
                self.assertEqual(ctx.exception.code, 409)
                self.assertGreaterEqual(centro.latidos.get("documentos", 0), 1, "crear avisa a la interfaz")
            finally:
                http.shutdown()
                http.server_close()
                hilo.join()

    def test_cli(self):
        from halo.documentos import cli
        import io
        from contextlib import redirect_stdout
        salida = io.StringIO()
        with redirect_stdout(salida):
            self.assertEqual(cli.main([str(self.carpeta), "memoria"]), 0)
            self.assertEqual(cli.main([str(self.carpeta), "guia"]), 0)
        datos = json.loads(salida.getvalue().split("\n{", 1)[0])
        self.assertEqual(datos["nombre"], "memoria.md")


if __name__ == "__main__":
    unittest.main()
