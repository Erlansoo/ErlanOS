"""Mallas mediante BMesh; bpy sólo aloja, guarda y exporta la escena nativa."""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix, Vector


def main(request, output):
    data, out = json.loads(Path(request).read_text(encoding='utf-8')), Path(output)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 0.001
    meshes, scene = {}, []
    try:
        for op in data['operations']:
            if op['engine'] != 'bmesh':
                continue
            p, kind, oid = op['params'], op['kind'], op['id']
            bm = meshes[op['inputs'][0]].copy() if op['inputs'] else bmesh.new()
            meshes[oid] = bm
            if kind == 'box':
                bmesh.ops.create_cube(bm, size=1)
                bmesh.ops.scale(bm, verts=list(bm.verts), vec=Vector((p['width'], p['depth'], p['height'])))
            elif kind == 'sphere':
                bmesh.ops.create_icosphere(bm, subdivisions=p['subdivisions'], radius=p['radius'])
            elif kind == 'cone':
                bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=p['segments'], radius1=p['radius1'], radius2=p['radius2'], depth=p['height'])
            elif kind == 'bevel':
                bmesh.ops.bevel(bm, geom=list(bm.edges), offset=p['radius'], segments=p['segments'], affect='EDGES', clamp_overlap=True)
            elif kind == 'subdivide':
                if len(bm.faces) * (p['cuts'] + 1) ** 2 > 60000:
                    raise ValueError('La subdivisión supera el presupuesto de geometría')
                bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=p['cuts'], use_grid_fill=True)
            elif kind == 'smooth':
                for _ in range(p['iterations']):
                    bmesh.ops.smooth_vert(bm, verts=list(bm.verts), factor=p['factor'], use_axis_x=True, use_axis_y=True, use_axis_z=True)
            elif kind == 'transform':
                bmesh.ops.scale(bm, verts=list(bm.verts), vec=Vector(p['scale']))
                rx, ry, rz = map(math.radians, p['rotation'])
                rotation = Matrix.Rotation(rz, 4, 'Z') @ Matrix.Rotation(ry, 4, 'Y') @ Matrix.Rotation(rx, 4, 'X')
                bmesh.ops.transform(bm, verts=list(bm.verts), matrix=rotation)
                bmesh.ops.translate(bm, verts=list(bm.verts), vec=Vector(p['translation']))
            else:
                raise ValueError('Operación no implementada: ' + kind)
            if 'position' in p:
                bmesh.ops.translate(bm, verts=list(bm.verts), vec=Vector(p['position']))
            bm.normal_update()
            if not bm.faces or len(bm.faces) > 60000:
                raise ValueError('Malla vacía o demasiado compleja')
            print('HALO_PROGRESS ' + json.dumps({'id': oid, 'label': op['label']}), flush=True)
        for op in data['operations']:
            if op['engine'] != 'bmesh':
                continue
            oid, bm = op['id'], meshes[op['id']]
            bm.verts.index_update()
            positions = [float(c) for v in bm.verts for c in v.co]
            triangles = [[loop.vert.index for loop in tri] for tri in bm.calc_loop_triangles()]
            if len(triangles) > 150000:
                raise ValueError('Demasiados triángulos')
            closed = all(e.is_manifold for e in bm.edges)
            scene.append({'id': oid, 'engine': 'bmesh', 'kind': op['kind'], 'label': op['label'], 'visible': op['display'],
                          'color': op['color'], 'positions': positions, 'indices': [i for tri in triangles for i in tri], 'lines': [],
                          'bounds': [min(v.co[i] for v in bm.verts) for i in range(3)] + [max(v.co[i] for v in bm.verts) for i in range(3)],
                          'stats': {'vertices': len(bm.verts), 'faces': len(bm.faces), 'triangles': len(triangles),
                                    'closed': closed, 'volume_mm3': abs(bm.calc_volume()) if closed else None}})
            mesh = bpy.data.meshes.new(oid)
            bm.to_mesh(mesh)
            obj = bpy.data.objects.new(op['label'], mesh)
            obj['halo_id'] = oid
            bpy.context.collection.objects.link(obj)
            obj.hide_render = not op['display']
            obj.hide_set(not op['display'])
            if op['display']:
                # STL is unitless; the document and export metadata explicitly say mm.
                with (out / (oid + '.stl')).open('w', encoding='ascii') as f:
                    f.write('solid halo\n')
                    vertices = list(bm.verts)
                    for tri in triangles:
                        a, b, c = [vertices[i].co for i in tri]
                        normal = (b-a).cross(c-a).normalized()
                        f.write('facet normal %g %g %g\nouter loop\n' % tuple(normal))
                        for v in (a, b, c):
                            f.write('vertex %.9g %.9g %.9g\n' % tuple(v))
                        f.write('endloop\nendfacet\n')
                    f.write('endsolid halo\n')
        bpy.ops.wm.save_as_mainfile(filepath=str(out / 'document.blend'))
        (out / 'bmesh-scene.json').write_text(json.dumps({'version': bpy.app.version_string, 'objects': scene}, allow_nan=False), encoding='utf-8')
    finally:
        for bm in meshes.values():
            bm.free()


if __name__ == '__main__':
    try:
        args = sys.argv[sys.argv.index('--') + 1:]
        main(*args)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
