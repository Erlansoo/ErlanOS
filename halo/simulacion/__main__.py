"""CLI para agentes: python -m halo.simulacion --help."""
import argparse
import json
from pathlib import Path
import time

from ..kicad.proyecto import revision
from .servicio import SERVICIO


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ejemplo', choices=['rc', 'rc_ac', 'rc_op', '555'])
    p.add_argument('--esquematico', type=Path)
    p.add_argument('--config', type=Path, help='Configuración JSON, sin alterar el esquemático')
    p.add_argument('--plantilla', action='store_true', help='Exporta inventario y plantilla JSON sin simular')
    p.add_argument('--timeout', type=int, default=60)
    a = p.parse_args()
    actual, peticion = {}, {}
    if a.ejemplo:
        peticion['ejemplo'] = a.ejemplo
    elif a.esquematico:
        sch = a.esquematico.resolve()
        actual = {'archivos': {'esquematico': str(sch)}}
        peticion = {'proyecto_id': str(sch.parent), 'expected_revision': revision(sch.parent)}
        if a.config:
            peticion['config'] = json.loads(a.config.read_text(encoding='utf-8-sig'))
    else:
        p.error('Indica --ejemplo o --esquematico')
    if a.plantilla:
        print(json.dumps(SERVICIO.inventario(actual, peticion), ensure_ascii=False, indent=2))
        return
    m = SERVICIO.preparar(actual, peticion)
    SERVICIO.ejecutar(m['id'], a.timeout)
    try:
        while SERVICIO.leer(m['id'], False)['estado'] in ('ejecutando', 'cancelando'):
            time.sleep(0.1)
    except KeyboardInterrupt:
        SERVICIO.cancelar(m['id'])
        raise
    resultado = SERVICIO.leer(m['id'])
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    if resultado['estado'] != 'completado':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
