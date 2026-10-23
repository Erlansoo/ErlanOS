"""Contrato y ciclo real de FreeCAD/BMesh; requiere los motores instalados."""
import math
import tempfile
import time
import unittest
from pathlib import Path

from halo.espacio3d.contrato import Error3D, ejemplo, validar_operaciones
from halo.espacio3d.servicio import Servicio3D, motores


class Contrato(unittest.TestCase):
    def test_rejects_invalid_graph_and_numbers(self):
        for operations in (
            [{'id': 'a', 'engine': 'freecad', 'kind': 'box', 'params': {'width': float('nan')}}],
            [{'id': 'a', 'engine': 'freecad', 'kind': 'cut', 'inputs': ['b', 'c']}],
            [{'id': '../a', 'engine': 'bmesh', 'kind': 'box'}],
            [{'id': 'a', 'engine': 'bmesh', 'kind': 'box'},
             {'id': 'b', 'engine': 'freecad', 'kind': 'extrude', 'inputs': ['a']}],
        ):
            with self.subTest(operations=operations), self.assertRaises(Error3D):
                validar_operaciones(operations)


@unittest.skipUnless(all(m['available'] for m in motores().values()), 'Motores no instalados')
class Integracion(unittest.TestCase):
    def test_native_persistence_revisions_failure_and_cancel(self):
        with tempfile.TemporaryDirectory(prefix='halo3d-test-') as base:
            service = Servicio3D(base)
            doc = service.crear('Prueba mixta')
            pid = doc['id']

            def execute(op, **args):
                return service.operar(dict(op=op, project=pid,
                    expected_revision=service.documento(pid)['revision'], **args))

            def wait(result):
                limit = time.monotonic() + 90
                while time.monotonic() < limit:
                    job = service.trabajo(result['job']['id'])
                    if job['status'] not in ('queued', 'running', 'cancelling'):
                        return job
                    time.sleep(.05)
                self.fail('Motor no terminó')

            job = wait(execute('append', operations=ejemplo('mixto')))
            self.assertEqual(job['status'], 'complete', job)
            first = service.documento(pid)
            scene = service.escena(pid, first['revision'])
            support = next(o for o in scene['objects'] if o['id'] == 'soporte')
            self.assertAlmostEqual(support['stats']['volume_mm3'], (64*40-math.pi*25)*6, places=5)
            for name in ('document.FCStd', 'document.blend', 'soporte.step', 'escultura.stl'):
                self.assertGreater(service.archivo(pid, first['revision'], name).stat().st_size, 100)
            with self.assertRaises(Error3D):
                service.archivo(pid, first['revision'], '../project.json')
            with self.assertRaises(Error3D):
                service.operar(dict(op='undo', project=pid, expected_revision=doc['revision']))
            self.assertEqual(wait(execute('update', id='base', params={'length': 8}))['status'], 'complete')
            second = service.documento(pid)
            undone = execute('undo')
            self.assertEqual(undone['head'], first['head'])
            self.assertNotEqual(undone['revision'], first['revision'])
            redone = execute('redo')
            self.assertEqual(redone['head'], second['head'])
            before = redone['revision']
            failed = wait(execute('append', operations=[{'id': 'invalid_fillet', 'engine': 'freecad',
                'kind': 'fillet', 'inputs': ['soporte'], 'params': {'radius': 9999}}]))
            self.assertEqual(failed['status'], 'failed', failed)
            self.assertEqual(service.documento(pid)['revision'], before)
            result = execute('update', id='base', params={'length': 7})
            service.cancelar(result['job']['id'])
            self.assertEqual(wait(result)['status'], 'cancelled')
            self.assertEqual(service.documento(pid)['revision'], before)
            reopened = Servicio3D(base)
            self.assertEqual(reopened.documento(pid)['head'], second['head'])
            self.assertEqual(reopened.activo(), pid)


if __name__ == '__main__':
    unittest.main()
