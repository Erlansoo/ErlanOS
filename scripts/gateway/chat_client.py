#!/usr/bin/env python3
"""Cliente ELP1 de terminal: lo mismo que hara el servicio `nube` de ErlanOS, desde el anfitrion.

    python scripts/gateway/chat_client.py --port 4040 "hola"          # un turno y sale
    python scripts/gateway/chat_client.py --port 4040                 # interactivo

Sirve para depurar la pasarela sin QEMU y como referencia del lado cliente.
"""
from __future__ import annotations
import argparse
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elp1  # noqa: E402


def connect(host: str, port: int, token: str, name: str = "chat_client") -> elp1.Link:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    session = random.randint(1, 0xFFFFFFFF)
    body = token.encode()[:16].ljust(16, b"\0") + name.encode()
    link = elp1.Link(sock, (host, port), session)
    if not link.send(elp1.HELLO, body):
        raise SystemExit("sin respuesta de la pasarela (HELLO sin ACK)")
    item = link.recv(5.0)
    if item is None or item[0] != elp1.HELLO_OK:
        raise SystemExit(f"saludo rechazado: {item}")
    print(f"[enlace] agente={item[2].decode()}", flush=True)
    return link


def turn(link: elp1.Link, text: str, timeout: float) -> str:
    if not link.send_text(elp1.USER, text):
        raise SystemExit("enlace caido al enviar")
    parts = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = link.recv(1.0)
        if item is None:
            continue
        kind, flags, body = item
        if kind == elp1.STATUS:
            print(f"  [estado] {body.decode(errors='replace')}", flush=True)
        elif kind == elp1.REPLY:
            piece = body.decode(errors="replace")
            parts.append(piece)
            print(piece, end="", flush=True)
            if flags & elp1.FLAG_LAST:
                print(flush=True)
                return "".join(parts)
        elif kind == elp1.ERROR:
            raise SystemExit("ERROR: " + body.decode(errors="replace"))
        elif kind == elp1.PING:
            link.send(elp1.PONG)
    raise SystemExit("tiempo agotado esperando la respuesta")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("message", nargs="?")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4040)
    parser.add_argument("--token", default="erlanos-pasarela-v1")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    link = connect(args.host, args.port, args.token)
    if args.message:
        turn(link, args.message, args.timeout)
        return
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line.strip():
            turn(link, line, args.timeout)


if __name__ == "__main__":
    main()
