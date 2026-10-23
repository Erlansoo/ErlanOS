"""Reliability regressions; never saves a KiCad design. Run with KiCad Python."""
from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from halo.kicad import proyecto, oraculo, sexpr as sx
from halo.kicad.biblioteca import KICAD_DIR
from halo.kicad.esquematico import Esquematico
from halo.kicad.verificador import informe_json
from halo.contexto import consultar


class Informes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'informe.json'

    def run_report(self, runner):
        return informe_json(['-o', str(self.out)], self.out, runner, ('violations', 'unconnected_items'))

    def test_error_no_es_cero(self):
        with self.assertRaises(RuntimeError):
            self.run_report(lambda args: subprocess.CompletedProcess(args, 1, '', 'fallo'))

    def test_informe_antiguo_no_sirve(self):
        self.out.write_text('{"sheets": []}')
        with patch.object(oraculo, '_run', return_value=subprocess.CompletedProcess([], 1, '', 'fallo')):
            with self.assertRaises(RuntimeError):
                oraculo.erc(Path('no-se-abre.kicad_sch'), self.out)

    def test_codigo_cero_sin_informe(self):
        with self.assertRaises(RuntimeError):
            self.run_report(lambda args: subprocess.CompletedProcess(args, 0, '', ''))

    def test_esquema_obligatorio(self):
        def runner(args):
            Path(args[-1]).write_text('{}')
            return subprocess.CompletedProcess(args, 0, '', '')
        with self.assertRaises(RuntimeError):
            self.run_report(runner)

    def test_timeout_no_es_cero(self):
        def runner(args):
            raise subprocess.TimeoutExpired(args, 1)
        with self.assertRaises(RuntimeError):
            self.run_report(runner)

    def test_informe_nuevo_publicado(self):
        self.out.write_text('anterior')
        def runner(args):
            self.assertNotEqual(args[-1], str(self.out))
            Path(args[-1]).write_text('{"violations": [], "unconnected_items": []}')
            return subprocess.CompletedProcess(args, 0, '', '')
        self.assertEqual(self.run_report(runner)['violations'], [])
        self.assertEqual(json.loads(self.out.read_text())['unconnected_items'], [])


class Identidades(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ruta = KICAD_DIR / 'share/kicad/demos/ecc83/ecc83-pp.kicad_sch'
        cls.raw = sx.loads(cls.ruta.read_text(encoding='utf-8'))

    def abrir(self):
        # Bypass the lock-file wrapper for this read-only demo inspection.
        return Esquematico.abrir.__wrapped__(Esquematico, self.ruta)

    def test_todas_las_unidades(self):
        e = self.abrir()
        self.assertEqual(len(e.simbolos), len(sx.find_all(self.raw, 'symbol')))
        self.assertEqual(len(e.simbolos.candidatos('U1')), 3)
        salida = e.a_sexpr()
        self.assertEqual(len(sx.find_all(salida, 'symbol')), len(sx.find_all(self.raw, 'symbol')))

    def test_ref_ambigua_exige_uuid(self):
        e = self.abrir()
        with self.assertRaises(ValueError):
            e._buscar('U1')
        for s in e.simbolos.candidatos('U1'):
            self.assertIs(e._buscar(s.uuid), s)

    def test_uuid_pin_e_instancia_estables(self):
        e = self.abrir()
        originales = {str(sx.val(n, 'uuid')): n for n in sx.find_all(self.raw, 'symbol')}
        for nodo in sx.find_all(e.a_sexpr(), 'symbol'):
            viejo = originales[str(sx.val(nodo, 'uuid'))]
            self.assertEqual(sx.find(nodo, 'instances'), sx.find(viejo, 'instances'))
            antiguos = {str(p[1]): sx.val(p, 'uuid') for p in sx.find_all(viejo, 'pin')}
            nuevos = {str(p[1]): sx.val(p, 'uuid') for p in sx.find_all(nodo, 'pin')}
            for numero, ident in antiguos.items():
                self.assertEqual(nuevos[numero], ident)

    def test_dnp_y_bom_preservados(self):
        e = self.abrir()
        nodo = copy.deepcopy(sx.find_all(self.raw, 'symbol')[0])
        for key, valor in [('dnp', 'yes'), ('in_bom', 'no'), ('exclude_from_sim', 'yes')]:
            bandera = sx.find(nodo, key)
            if bandera is None:
                nodo.append([sx.Sym(key), sx.Sym(valor)])
            else:
                bandera[1] = sx.Sym(valor)
        salida = e._nodo_simbolo(e._leer_simbolo(nodo))
        for key in ('dnp', 'in_bom', 'exclude_from_sim'):
            self.assertEqual(sx.val(salida, key), sx.val(nodo, key))


class Proyectos(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / '.halo').mkdir()
        self.dato = self.dir / '.halo/intencion.json'
        self.dato.write_text('{}')

    def test_revision_obsoleta(self):
        rev = proyecto.revision(self.dir)
        self.dato.write_text('{"nuevo": true}')
        with self.assertRaises(proyecto.ConflictoProyecto):
            proyecto.comprobar(self.dir, rev)

    def test_lock_reentrante(self):
        with proyecto.bloquear(self.dir):
            with proyecto.bloquear(self.dir):
                self.assertEqual(proyecto.comprobar(self.dir, proyecto.revision(self.dir)), proyecto.revision(self.dir))

    def test_otro_proceso_rechazado_y_luego_libre(self):
        codigo = ('from pathlib import Path; from halo.kicad.proyecto import bloquear; '
                  f'\nwith bloquear(Path({str(self.dir)!r})): print("adquirido")')
        with proyecto.bloquear(self.dir):
            r = subprocess.run([sys.executable, '-c', codigo], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('ConflictoProyecto', r.stderr)
        r = subprocess.run([sys.executable, '-c', codigo], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_error_restaura_estado(self):
        class Motor:
            @proyecto.operacion
            def fallar(m):
                (m.carpeta / '.halo/intencion.json').write_text('{"incorrecto":true}')
                raise ValueError('fallo simulado')
        m = Motor()
        m.carpeta, m._revision_proyecto = self.dir, proyecto.revision(self.dir)
        with self.assertRaises(ValueError):
            m.fallar()
        self.assertEqual(self.dato.read_text(), '{}')
        with self.assertRaises(proyecto.ConflictoProyecto):
            m.fallar()

    def test_rechazo_sin_escritura_no_invalida(self):
        class Motor:
            vueltas = 0

            def _marca_memoria(m):
                return m.vueltas

            def _volver_memoria(m, marca, cambiados):
                m.vueltas = marca

            @proyecto.operacion
            def rechazar(m):
                m.vueltas += 1
                raise ValueError('choque simulado')

            @proyecto.operacion
            def seguir(m):
                return 'sigue'
        m = Motor()
        m.carpeta, m._revision_proyecto = self.dir, proyecto.revision(self.dir)
        with self.assertRaises(ValueError):
            m.rechazar()
        self.assertEqual(m.vueltas, 0)
        self.assertEqual(m.seguir(), 'sigue')

    def test_fallo_a_medias_restaura_recarga_y_sigue(self):
        class Motor:
            recargado = None

            def _marca_memoria(m):
                return None

            def _volver_memoria(m, marca, cambiados):
                m.recargado = cambiados

            @proyecto.operacion
            def fallar(m):
                (m.carpeta / '.halo/intencion.json').write_text('{"incorrecto":true}')
                raise ValueError('fallo a medias')

            @proyecto.operacion
            def seguir(m):
                return 'sigue'
        m = Motor()
        m.carpeta, m._revision_proyecto = self.dir, proyecto.revision(self.dir)
        with self.assertRaises(ValueError):
            m.fallar()
        self.assertEqual(self.dato.read_text(), '{}')
        self.assertTrue(m.recargado)
        self.assertEqual(m.seguir(), 'sigue')

    def test_verificacion_se_invalida(self):
        from halo.kicad import sesion
        s = sesion.Sesion.__new__(sesion.Sesion)
        s.archivo = self.dir / 'documento'
        s.datos = {'verificacion': {'revision': proyecto.revision(self.dir), 'erc': {'errores': 0}}}
        self.dato.write_text('{"cambio":true}')
        with patch.object(sesion, 'ACTUAL', self.dir / 'estado.json'):
            s._publicar()
        self.assertIsNone(s.datos['verificacion'])

    def test_etapa_verificada_se_reabre_al_editar(self):
        class Motor:
            @proyecto.operacion
            def mover(m):
                return m.etapa
        m = Motor()
        m.carpeta, m._revision_proyecto, m.etapa = self.dir, proyecto.revision(self.dir), 'verificada'
        self.assertEqual(m.mover(), 'ruteada')

    def test_editor_rechaza_antes_de_mutar(self):
        from halo import editor
        actual = {'halo': {'estado': 'reposo'}, 'archivos': {'esquematico': str(self.dir / 'documento')}}
        with patch.object(editor, '_actual', return_value=actual), patch.object(editor, '_editar_esquematico') as motor:
            with self.assertRaises(editor.ErrorEdicion):
                editor.editar({'etapa': 'esquematico', 'proyecto_id': str(self.dir.resolve()), 'expected_revision': 'antigua'})
            motor.assert_not_called()

    def test_editor_commit_y_revision(self):
        from halo import editor
        actual = {'halo': {'estado': 'reposo'}, 'archivos': {'esquematico': str(self.dir / 'documento')}}
        antes = proyecto.revision(self.dir)
        def cambio(a, p):
            self.dato.write_text('{"cambio":true}')
            return {'ok': True}
        with patch.object(editor, '_actual', return_value=actual), patch.object(editor, '_editar_esquematico', side_effect=cambio), \
             patch.object(editor, '_actualizar_actual') as publicar:
            r = editor.editar({'op': 'mover', 'etapa': 'esquematico', 'proyecto_id': str(self.dir.resolve()), 'expected_revision': antes})
            self.assertTrue(r['ok'])
            self.assertNotEqual(r['revision'], antes)
            self.assertIsNone(publicar.call_args.kwargs['verificacion'])


class ConstruccionEnMemoria(unittest.TestCase):
    def test_cableado_edicion_e_identidad(self):
        with tempfile.TemporaryDirectory() as d:
            e = Esquematico(Path(d), 'memoria')
            e.colocar('Device:R', 'R1', '1k', 50.8, 50.8)
            e.colocar('Device:LED', 'D1', 'LED', 76.2, 76.2)
            e.red('SENAL', ('R1', '1'), ('D1', '1'))
            e.red('RETORNO', ('R1', '2'), ('D1', '2'))
            ident = e.simbolos['R1'].uuid
            e.mover(ident, 2.54, 0)
            e._reconstruir_redes()
            redes = {frozenset(v) for v in e.redes.values()}
            self.assertIn(frozenset({('R1', '1'), ('D1', '1')}), redes)
            self.assertIn(frozenset({('R1', '2'), ('D1', '2')}), redes)
            self.assertEqual(e.simbolos['R1'].uuid, ident)
            self.assertEqual(len(sx.find_all(e.a_sexpr(), 'symbol')), 2)
            self.assertFalse(list(Path(d).glob('*.kicad_*')), 'El test no guarda diseños')


class Ruteo(unittest.TestCase):
    def test_reintento_no_toca_red_ajena(self):
        tree = ast.parse((ROOT / 'halo/kicad/placa.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Placa')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'rutear')
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'rutear', 'exec'), ns)
        cleared, routed = [], []
        def route(red, conservar=True):
            routed.append(red)
            return len(routed) > 1
        p = SimpleNamespace(etapa='acomodada', pistas=[], vias=[], board=SimpleNamespace(Zones=lambda: []),
            redes_a_rutear=lambda redes: ['TARGET'], _grupos_red=lambda red: [1, 2],
            _incompletas=lambda: [], capas_ruteo=lambda: [0, 1], _decir=lambda *args: None,
            _rutear_red=route, _vecinas=lambda red: ['OUTSIDE'],
            despejar=lambda red, **kw: cleared.append(red), _paso=lambda *args: None)
        ns['rutear'](p, ['TARGET'], reintentos=1)
        self.assertNotIn('OUTSIDE', cleared + routed)


class Contexto(unittest.TestCase):
    def test_multiunidades_y_consulta_red(self):
        modelos = {'esquematico': {'simbolos': [{'ref': 'U1', 'uuid': 'a'}, {'ref': 'U1', 'uuid': 'b'}]},
                   'pcb': {'huellas': [{'ref': 'U1', 'uuid': 'fp', 'pads': [{'num': '1', 'red': 'VCC', 'p': [1, 2]}]}]}}
        d = consultar({'revision': 'r'}, modelos, red='VCC')
        self.assertEqual(len(d['componentes'][0]['unidades']), 2)
        self.assertEqual(d['redes_pcb']['VCC'][0]['ref'], 'U1')
        self.assertEqual(d['redes_pcb']['VCC'][0]['pad'], '1')
        self.assertEqual(d['revision'], 'r')


class KiCadReal(unittest.TestCase):
    def test_verificadores_reales_sin_guardar_diseno(self):
        from halo.kicad.placa import Placa
        base = ROOT / 'halo/proyectos/parpadeo555'
        antes = proyecto.revision(base)
        with tempfile.TemporaryDirectory() as d:
            salida = Path(d)
            erc = oraculo.erc(base / 'parpadeo555.kicad_sch', salida / 'erc.json')
            self.assertIsInstance(erc['errores'], int)
            nets = oraculo.netlist(base / 'parpadeo555.kicad_sch', salida / 'netlist.net')
            self.assertGreater(len(nets), 0)
            p = SimpleNamespace(carpeta=salida, ruta=base / 'parpadeo555.kicad_pcb')
            drc = Placa._drc_rapido(p)
            self.assertEqual(set(drc), {'errores', 'sin_conectar'})
        self.assertEqual(proyecto.revision(base), antes, 'El diseño debe permanecer idéntico')


if __name__ == '__main__':
    unittest.main(verbosity=2)
