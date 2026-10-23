"""Compact, queryable design state for an agent; independent of its transport."""
CAPACIDADES = {
    'chat': {'endpoint': '/api/chat', 'alcance': 'Un chat de Claude Code por proyecto (varios por pestaña)',
             'transporte': 'binario oficial por stdin/stdout (stream-json, --permission-prompt-tool stdio)',
             'documentacion': 'docs/HALO.md («Chat de Claude Code por pestaña»)',
             'operaciones': ['nuevo', 'enviar', 'permiso', 'cancelar', 'bifurcar', 'consola', 'remoto', 'opciones', 'quitar']},
    'documentos': {'endpoint': '/api/documentos', 'cli': 'python -m halo.documentos.cli <carpeta> ...',
                   'documentacion': 'docs/HALO.md («Documentación viva»)'},
    'espacio3d': {'endpoint': '/api/espacio3d', 'scene': '/api/espacio3d/scene',
                  'engines': ['freecad', 'bmesh'], 'units': 'mm', 'up': 'Z',
                  'requires': ['project', 'expected_revision'],
                  'operations': ['create', 'open', 'append', 'update', 'remove', 'undo', 'redo', 'cancel', 'note'],
                  'cli': 'python -m halo.espacio3d --help',
                  'documentation': 'docs/HALO-ESPACIO-3D.md',
                  'limits': 'Catálogo acotado. Chat conectado mediante /api/agentes; las notas antiguas no se ejecutan automáticamente.'},
    'bloques': {'endpoint': '/api/bloques', 'consulta': ['proyecto_id', 'uuid'],
                'documento': '.halo/bloques.json', 'interaccion': 'Doble clic en borde o título del bloque',
                'contenido': ['funcion', 'decisiones', 'fuentes', 'pruebas', 'pendientes'],
                'limite': 'La documentación no certifica el circuito; cada evidencia conserva su revisión.'},
    'fabricacion': {'endpoint': '/api/fabricacion', 'requiere': ['proyecto_id', 'expected_revision'],
                    'salidas': ['Gerber X2', 'Excellon PTH/NPTH', 'DRC', 'manifiesto SHA256'],
                    'alcance': 'PCB sin montaje; exportación para revisión, sin pedidos automáticos',
                    'documentacion': 'docs/HALO-FABRICACION.md'},
    'version': 2,
    'simulacion': {'endpoint': '/api/simulacion',
                   'operaciones': ['inventario', 'configurar', 'preparar', 'ejecutar', 'cancelar', 'comparar'],
                   'analisis': ['op', 'tran', 'ac'], 'motor': 'ngspice en proceso separado',
                   'requiere_proyecto': ['proyecto_id', 'expected_revision'],
                   'configuracion': '.halo/simulacion/config.json',
                   'cli': 'python -m halo.simulacion --help',
                   'documentacion': 'docs/HALO-SIMULACION.md',
                   'limite': 'Requiere modelos y banco explícitos. No modifica ni aprueba el diseño.'},
    'jev': {'cli': 'python -m halo.jev "estado" -p "¿pregunta?" -o A=texto -o B=texto  (o --si-no / --escala)',
            'alcance': 'Segunda opinión de Jev (TypeSafe): una decisión tipada con probabilidad y confianza sobre lo que va en el estado',
            'limite': 'No sabe datasheets ni inventario: sólo juzga lo descrito; una respuesta DUDOSA no cuenta. Requiere TYPESAFE_API_KEY del usuario.'},
    'lectura': ['/api/contexto', '/api/estado', '/api/capacidades'],
    'edicion': {'endpoint': '/api/editar', 'requiere': ['proyecto_id', 'expected_revision', 'etapa', 'op'],
                'esquematico': ['mover', 'rotar', 'mover_etiqueta', 'mover_bloque', 'mover_campo', 'mover_cable', 'trazar_cable', 'borrar_cable', 'rotar_etiqueta', 'espejar', 'deshacer', 'verificar'],
                'pcb': ['mover', 'rotar', 'voltear', 'deshacer', 'verificar']},
    'motor_directo': 'halo.kicad.esquematico.Esquematico / halo.kicad.placa.Placa',
    'limites': ['Jerarquía y buses: visualización', 'Sin ruteo diferencial acoplado', 'Conectividad del visor PCB aproximada'],
}


def consultar(actual, modelos, ref=None, red=None):
    componentes = {}
    for s in modelos.get('esquematico', {}).get('simbolos', []):
        if s.get('power'):
            continue
        c = componentes.setdefault(s['ref'], {'ref': s['ref'], 'unidades': [], 'huellas': []})
        c['unidades'].append({k: s.get(k) for k in ('uuid', 'unidad', 'valor', 'lib_id', 'at', 'rot')})
    redes = {}
    for h in modelos.get('pcb', {}).get('huellas', []):
        c = componentes.setdefault(h['ref'], {'ref': h['ref'], 'unidades': [], 'huellas': []})
        c['huellas'].append({k: h.get(k) for k in ('uuid', 'valor', 'at', 'ang', 'lado')})
        for pad in h.get('pads', []):
            nombre = pad.get('red')
            if nombre:
                redes.setdefault(nombre, []).append({'ref': h['ref'], 'pad': pad.get('num'), 'posicion': pad.get('p')})
    if red:
        redes = {k: v for k, v in redes.items() if k == red}
        refs = {p['ref'] for pads in redes.values() for p in pads}
        componentes = {k: v for k, v in componentes.items() if k in refs}
    if ref:
        componentes = {k: v for k, v in componentes.items() if k == ref}
        redes = {k: v for k, v in redes.items() if any(p['ref'] == ref for p in v)}
    return {'ok': True, 'proyecto_id': actual.get('proyecto_id'), 'revision': actual.get('revision'),
            'proyecto': actual.get('proyecto'), 'verificacion': actual.get('verificacion'),
            'componentes': list(componentes.values()), 'redes_pcb': redes,
            'conectividad': 'Pads declarados en PCB; no equivale a ERC, DRC ni paridad verificada',
            'bloques': modelos.get('esquematico', {}).get('bloques', [])}
