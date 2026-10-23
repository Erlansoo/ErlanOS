#!/usr/bin/env python3
"""Prueba de la pasarela en loopback con el agente echo (sin credenciales ni QEMU).

Comprueba: token invalido, saludo, respuesta por trozos, mensaje largo (varios
trozos), ACK perdido con retransmision y descarte del duplicado, y cancelacion.
"""
from __future__ import annotations
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import elp1  # noqa: E402

PORT = 4047
TOKEN = b"prueba-elp1-0001"


class Failure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


def start_gateway(ready: Path) -> subprocess.Popen:
    cmd = [sys.executable, str(HERE / "erlan_gateway.py"), "--agent", "echo", "--bind", "127.0.0.1",
           "--port", str(PORT), "--token", TOKEN.decode(), "--max-sessions", "1", "--ready-file", str(ready)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    for _ in range(100):
        if ready.exists():
            return proc
        if proc.poll() is not None:
            raise Failure("la pasarela termino antes de escuchar:\n" + proc.stdout.read())
        time.sleep(0.05)
    raise Failure("la pasarela no llego a escuchar")


def hello(sock, session, token, name=b"prueba"):
    sock.sendto(elp1.pack(elp1.HELLO, session, 1, 0, token + name), ("127.0.0.1", PORT))


def collect_reply(link: elp1.Link, timeout=5.0):
    """Recoge STATUS y REPLY hasta el trozo final; devuelve (estados, texto)."""
    statuses, parts = [], []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = link.recv(0.5)
        if item is None:
            continue
        kind, flags, body = item
        if kind == elp1.STATUS:
            statuses.append(body.decode())
        elif kind == elp1.REPLY:
            parts.append(body)
            if flags & elp1.FLAG_LAST:
                return statuses, b"".join(parts).decode()
        elif kind == elp1.ERROR:
            raise Failure("ERROR de la pasarela: " + body.decode())
    raise Failure("no llego el final de la respuesta")


def main():
    ready = Path(tempfile.mkdtemp()) / "ready"
    proc = start_gateway(ready)
    results = []
    try:
        # 1. Token invalido -> ERROR
        bad = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bad.settimeout(2)
        hello(bad, 0x11, b"token-incorrecto")
        raw, _ = bad.recvfrom(2048)
        parsed = elp1.unpack(raw)
        check(parsed and parsed[0] == elp1.ERROR, "token invalido no produjo ERROR")
        bad.close()
        results.append("token invalido rechazado")

        # 2. Saludo valido -> ACK + HELLO_OK
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        session = 0x5A5A0001
        hello(sock, session, TOKEN)
        link = elp1.Link(sock, ("127.0.0.1", PORT), session)
        link.seq = 1
        item = link.recv(3.0)
        check(item is not None and item[0] == elp1.HELLO_OK, f"esperaba HELLO_OK, llego {item}")
        check(item[2] == b"echo", f"nombre del agente inesperado: {item[2]!r}")
        results.append("saludo aceptado, agente=echo")

        # 3. Mensaje corto -> STATUS + REPLY con final
        check(link.send_text(elp1.USER, "hola"), "USER sin ACK")
        statuses, text = collect_reply(link)
        check(text == "eco: hola", f"respuesta inesperada: {text!r}")
        check(statuses == ["eco: pensando"], f"estados inesperados: {statuses}")
        results.append("respuesta corta correcta")

        # 4. Mensaje largo con acentos (varios trozos en ambos sentidos)
        long_text = ("Márgenes espejo y páginas ñ " * 60).strip()
        check(len(long_text.encode()) > elp1.BODY_MAX, "el texto largo no supera un trozo")
        check(link.send_text(elp1.USER, long_text), "USER largo sin ACK")
        _, text = collect_reply(link)
        check(text == "eco: " + long_text, "el texto largo no volvio intacto")
        results.append(f"mensaje largo de {len(long_text.encode())} bytes intacto")

        # 5. ACK perdido: ignoramos el primer REPLY sin confirmar; la pasarela retransmite
        check(link.send_text(elp1.USER, "ack perdido"), "USER sin ACK")
        sock.settimeout(3)
        dropped = None
        while dropped is None:
            raw, _ = sock.recvfrom(2048)
            parsed = elp1.unpack(raw)
            if parsed and parsed[0] == elp1.REPLY:
                dropped = parsed
            elif parsed and parsed[0] == elp1.STATUS:
                link._accept(parsed)
        t0 = time.monotonic()
        _, text = collect_reply(link)
        elapsed = time.monotonic() - t0
        check(text == "eco: ack perdido", f"tras la retransmision llego {text!r}")
        check(elapsed >= 0.3, f"no hubo espera de retransmision ({elapsed:.2f}s)")
        results.append(f"ACK perdido: retransmitido a los {elapsed:.2f}s y sin duplicado")

        # 6. Cancelacion durante un turno lento
        check(link.send_text(elp1.USER, "/lento"), "USER sin ACK")
        time.sleep(0.3)
        check(link.send(elp1.CANCEL), "CANCEL sin ACK")
        _, text = collect_reply(link)
        check(text == "eco: cancelado", f"la cancelacion no surtio efecto: {text!r}")
        results.append("cancelacion atendida")
        sock.close()
    except Failure as failure:
        proc.kill()
        print("GATEWAY FAIL:", failure)
        print(proc.stdout.read())
        return 1
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    for line in results:
        print("  -", line)
    print("GATEWAY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
