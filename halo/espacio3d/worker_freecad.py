"""Proceso aislado: API FreeCAD/Part/Sketcher, nunca FreeCADGui ni MCP."""
import json
import math
import sys
import traceback
from pathlib import Path

import FreeCAD as App
import Part
import Sketcher


def main(request, output):
    data = json.loads(Path(request).read_text(encoding='utf-8'))
    out = Path(output)
    doc = App.newDocument('Halo')
    objects, scene = {}, []
    for op in data['operations']:
        if op['engine'] != 'freecad':
            continue
        p, kind, oid = op['params'], op['kind'], op['id']
        sources = [objects[x] for x in op['inputs']]
        if kind == 'box':
            obj = doc.addObject('Part::Box', oid)
            obj.Length, obj.Width, obj.Height = p['width'], p['depth'], p['height']
        elif kind in ('cylinder', 'sphere', 'cone'):
            obj = doc.addObject({'cylinder': 'Part::Cylinder', 'sphere': 'Part::Sphere', 'cone': 'Part::Cone'}[kind], oid)
            if kind == 'cone':
                obj.Radius1, obj.Radius2 = p['radius1'], p['radius2']
            else:
                obj.Radius = p['radius']
            if kind != 'sphere':
                obj.Height = p['height']
        elif kind.startswith('sketch_'):
            obj = doc.addObject('Sketcher::SketchObject', oid)
            if kind == 'sketch_rectangle':
                w, d = p['width'], p['depth']
                points = [App.Vector(0, 0, 0), App.Vector(w, 0, 0), App.Vector(w, d, 0), App.Vector(0, d, 0)]
                for a, b in zip(points, points[1:] + points[:1]):
                    idx = obj.addGeometry(Part.LineSegment(a, b), False)
                    obj.addConstraint(Sketcher.Constraint('Block', idx))
            else:
                idx = obj.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), p['radius']), False)
                obj.addConstraint(Sketcher.Constraint('Block', idx))
        elif kind == 'extrude':
            obj = doc.addObject('Part::Extrusion', oid)
            obj.Base = sources[0]
            obj.DirMode = 'Normal'
            obj.LengthFwd, obj.LengthRev, obj.Solid = p['length'], 0, True
        elif kind in ('cut', 'fuse', 'common'):
            obj = doc.addObject({'cut': 'Part::Cut', 'fuse': 'Part::Fuse', 'common': 'Part::Common'}[kind], oid)
            obj.Base, obj.Tool = sources
            obj.Refine = True
        elif kind in ('fillet', 'chamfer'):
            obj = doc.addObject('Part::Fillet' if kind == 'fillet' else 'Part::Chamfer', oid)
            obj.Base = sources[0]
            edges = p['edges'] or list(range(1, len(sources[0].Shape.Edges) + 1))
            if any(i > len(sources[0].Shape.Edges) for i in edges):
                raise ValueError('La arista seleccionada no existe en esta revisión')
            radius = p.get('radius', p.get('distance'))
            obj.Edges = [(i, radius, radius) for i in edges]
        elif kind == 'transform':
            obj = doc.addObject('Part::Feature', oid)
            obj.addProperty('App::PropertyLink', 'Source', 'Halo').Source = sources[0]
            obj.Shape = sources[0].Shape.copy()
            rx, ry, rz = p['rotation']
            rotation = App.Rotation(App.Vector(0, 0, 1), rz).multiply(App.Rotation(App.Vector(0, 1, 0), ry)).multiply(App.Rotation(App.Vector(1, 0, 0), rx))
            transform = App.Placement(App.Vector(*p['translation']), rotation)
            obj.Placement = transform.multiply(obj.Placement)
        else:
            raise ValueError('Operación no implementada: ' + kind)
        if 'position' in p:
            obj.Placement.Base = App.Vector(*p['position'])
        obj.Label = op['label']
        obj.addProperty('App::PropertyString', 'HaloId', 'Halo').HaloId = oid
        doc.recompute()
        if obj.Shape.isNull() or not obj.Shape.isValid():
            raise ValueError(f'{oid}: FreeCAD rechazó la geometría')
        if kind not in ('sketch_rectangle', 'sketch_circle') and not obj.Shape.Solids:
            raise ValueError(f'{oid}: la operación no produjo un sólido')
        objects[oid] = obj
        print('HALO_PROGRESS ' + json.dumps({'id': oid, 'label': op['label']}), flush=True)
    doc.recompute()
    for op in data['operations']:
        if op['engine'] != 'freecad':
            continue
        obj, oid = objects[op['id']], op['id']
        shape = obj.Shape
        points, triangles = shape.tessellate(0.15) if shape.Faces else ([], [])
        if len(triangles) > 150000:
            raise ValueError('La malla supera el límite de complejidad del visor')
        positions = [float(c) for v in points for c in (v.x, v.y, v.z)]
        lines = []
        for edge in shape.Edges[:1500]:
            pts = edge.discretize(Deflection=0.15)
            for a, b in zip(pts, pts[1:]):
                lines.extend([a.x, a.y, a.z, b.x, b.y, b.z])
        bb = shape.BoundBox
        scene.append({'id': oid, 'engine': 'freecad', 'kind': op['kind'], 'label': op['label'], 'visible': op['display'],
                      'color': op['color'], 'positions': positions, 'indices': [i for tri in triangles for i in tri], 'lines': lines,
                      'bounds': [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax],
                      'stats': {'faces': len(shape.Faces), 'edges': len(shape.Edges), 'solids': len(shape.Solids),
                                'volume_mm3': shape.Volume, 'valid': shape.isValid(), 'triangles': len(triangles)}})
        if op['display'] and shape.Solids:
            shape.exportStep(str(out / (oid + '.step')))
            shape.exportStl(str(out / (oid + '.stl')))
    doc.saveAs(str(out / 'document.FCStd'))
    (out / 'freecad-scene.json').write_text(json.dumps({'version': App.Version()[:3], 'objects': scene}, allow_nan=False), encoding='utf-8')
    App.closeDocument(doc.Name)


if __name__ == '__main__':
    try:
        main(sys.argv[1], sys.argv[2])
    except Exception:
        traceback.print_exc()
        sys.exit(1)
