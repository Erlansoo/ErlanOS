"""ELP1 (Erlan Link Protocol v1): cabecera, tipos y enlace fiable de parada y espera.

Compartido por la pasarela y por las pruebas del anfitrion. Ver docs/PASARELA.md.
"""
from __future__ import annotations
import socket
import struct
import time

MAGIC = 0x31504C45
VERSION = 1
HEADER = struct.Struct("<IBBBBIIIHH")
HEADER_SIZE = HEADER.size  # 24
BODY_MAX = 1024
FLAG_LAST = 1

HELLO, HELLO_OK, USER, REPLY, STATUS, CANCEL, PING, PONG, ACK, ERROR = range(1, 11)
NAMES = {HELLO: "HELLO", HELLO_OK: "HELLO_OK", USER: "USER", REPLY: "REPLY", STATUS: "STATUS",
         CANCEL: "CANCEL", PING: "PING", PONG: "PONG", ACK: "ACK", ERROR: "ERROR"}

RETRY_MS = 400
RETRIES = 5


def pack(kind: int, session: int, seq: int, ack: int, body: bytes = b"", last: bool = False) -> bytes:
    if len(body) > BODY_MAX:
        raise ValueError("cuerpo mayor que 1024 bytes")
    flags = FLAG_LAST if last else 0
    return HEADER.pack(MAGIC, VERSION, kind, flags, 0, session, seq, ack, len(body), 0) + body


def unpack(datagram: bytes):
    """Devuelve (kind, flags, session, seq, ack, body) o None si el datagrama es invalido."""
    if len(datagram) < HEADER_SIZE:
        return None
    magic, version, kind, flags, _r0, session, seq, ack, length, _r1 = HEADER.unpack_from(datagram)
    if magic != MAGIC or version != VERSION or kind not in NAMES or length > BODY_MAX:
        return None
    if len(datagram) != HEADER_SIZE + length:
        return None
    return kind, flags, session, seq, ack, datagram[HEADER_SIZE:]


class Link:
    """Un extremo ELP1 sobre un socket UDP: envio fiable (parada y espera) y recepcion con ACK.

    Es deliberadamente simple para que el lado Erlan pueda replicarlo sin colas.
    """

    def __init__(self, sock: socket.socket, peer, session: int, log=None):
        self.sock = sock
        self.peer = peer
        self.session = session
        self.seq = 0            # ultima secuencia enviada por nosotros
        self.received = 0       # ultima secuencia recibida del otro lado (en orden)
        self.log = log or (lambda s: None)
        self.pending = []       # datagramas no ACK recibidos mientras esperabamos un ACK
        self.alive = True

    def _ack(self, seq: int):
        self.sock.sendto(pack(ACK, self.session, 0, seq), self.peer)

    def _accept(self, parsed):
        """Procesa un datagrama entrante que no es ACK: confirma y decide si es nuevo."""
        kind, flags, session, seq, ack, body = parsed
        if session != self.session:
            return None
        if kind == ACK:
            return None
        self._ack(seq)
        if seq <= self.received:       # repetido: ya confirmado, se descarta
            return None
        self.received = seq
        return kind, flags, body

    def send(self, kind: int, body: bytes = b"", last: bool = False) -> bool:
        """Envia y espera ACK; retransmite RETRIES veces. False si el enlace cayo."""
        self.seq += 1
        datagram = pack(kind, self.session, self.seq, self.received, body, last)
        for attempt in range(RETRIES + 1):
            self.sock.sendto(datagram, self.peer)
            deadline = time.monotonic() + RETRY_MS / 1000
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.sock.settimeout(remaining)
                try:
                    raw, addr = self.sock.recvfrom(2048)
                except socket.timeout:
                    break
                parsed = unpack(raw)
                if parsed is None or addr != self.peer:
                    continue
                if parsed[0] == ACK and parsed[2] == self.session and parsed[4] == self.seq:
                    return True
                accepted = self._accept(parsed)
                if accepted:
                    self.pending.append(accepted)
            if attempt < RETRIES:
                self.log(f"retransmision {attempt + 1} de {NAMES[kind]} seq={self.seq}")
        self.alive = False
        return False

    def recv(self, timeout: float):
        """Devuelve (kind, flags, body) del siguiente datagrama nuevo, o None si vence el plazo."""
        if self.pending:
            return self.pending.pop(0)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                raw, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                return None
            parsed = unpack(raw)
            if parsed is None or addr != self.peer:
                continue
            accepted = self._accept(parsed)
            if accepted:
                return accepted

    def send_text(self, kind: int, text: str) -> bool:
        """Parte un texto UTF-8 en trozos de <= 1024 bytes sin cortar un caracter."""
        data = text.encode("utf-8")
        if not data:
            return self.send(kind, b"", last=True)
        chunks = []
        start = 0
        while start < len(data):
            end = min(start + BODY_MAX, len(data))
            while end < len(data) and (data[end] & 0xC0) == 0x80:
                end -= 1
            chunks.append(data[start:end])
            start = end
        for i, chunk in enumerate(chunks):
            if not self.send(kind, chunk, last=(i == len(chunks) - 1)):
                return False
        return True
