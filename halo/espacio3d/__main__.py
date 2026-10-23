"""Cliente para agentes: python -m halo.espacio3d --help. No usa MCP."""
import argparse
import json
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8767')
    parser.add_argument('--request', help='Archivo JSON de una operación; omitir para consultar capacidades y proyectos')
    parser.add_argument('--project', help='Consultar documento, operaciones y notas pendientes')
    parser.add_argument('--job', help='Consultar progreso y errores de un trabajo')
    args = parser.parse_args()
    path = '/api/espacio3d'
    if args.project:
        path += '?project=' + args.project
    elif args.job:
        path += '?job=' + args.job
    body = Path(args.request).read_bytes() if args.request else None
    request = urllib.request.Request(args.url.rstrip('/') + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        data = json.load(exc)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
