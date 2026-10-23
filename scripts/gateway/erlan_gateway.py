#!/usr/bin/env python3
"""Pasarela ErlanOS <-> agente sin interfaz (Claude Code, Codex o eco de prueba).

    python scripts/gateway/erlan_gateway.py --agent claude --repo . --port 4040 --token erlanos-pasarela-v1
    python scripts/gateway/erlan_gateway.py --agent echo --port 4041      # pruebas sin credenciales

Escucha ELP1 por UDP (docs/PASARELA.md). Una sesion a la vez. ErlanOS nunca ve
credenciales: el agente corre aqui con la sesion iniciada del anfitrion.
"""
from __future__ import annotations
import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elp1  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPT = (
    "Hablas desde la pasarela con un usuario que escribe en el chat de ErlanOS, un sistema operativo propio "
    "escrito en el lenguaje Erlan. Tu repositorio de trabajo es el codigo de ErlanOS. Responde en espanol, "
    "breve y en prosa; el chat muestra texto plano de 8x16 sin markdown. Cuando trabajes sobre el repo, "
    "compila y prueba antes de afirmar que algo funciona."
)


def log(message: str):
    print(f"[pasarela] {message}", flush=True)


def find_executable(name: str, extra: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in extra:
        if Path(candidate).exists():
            return candidate
    return None


def claude_candidates() -> list[str]:
    roaming = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
    versions = sorted(roaming.glob("*/claude.exe"), key=lambda p: p.stat().st_mtime, reverse=True) if roaming.exists() else []
    return [str(p) for p in versions]


def codex_candidates() -> list[str]:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    return [str(p) for p in base.glob("*/codex.exe")] if base.exists() else []


class Agent:
    """Interfaz de un agente: turn(text, emit, cancelled) -> None. emit(kind, text, last)."""
    name = "agente"

    def turn(self, text: str, emit, cancelled) -> None:
        raise NotImplementedError

    def close(self):
        pass


class EchoAgent(Agent):
    name = "echo"

    def turn(self, text, emit, cancelled):
        emit(elp1.STATUS, "eco: pensando", False)
        if text.strip() == "/lento":
            for i in range(20):
                if cancelled():
                    emit(elp1.REPLY, "eco: cancelado", True)
                    return
                time.sleep(0.1)
        emit(elp1.REPLY, "eco: " + text, True)


class ClaudeAgent(Agent):
    """Claude Code en modo --print con stream-json en ambos sentidos; una sesion persistente."""

    def __init__(self, repo: Path, permission_mode: str, executable: str | None):
        self.repo = repo
        self.permission_mode = permission_mode
        self.exe = executable or find_executable("claude", claude_candidates())
        if not self.exe:
            raise SystemExit("no se encontro claude.exe; instala Claude Code o pasa --executable")
        self.name = "claude-code"
        self.session_id: str | None = None
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()

    def _spawn(self):
        cmd = [self.exe, "-p", "--verbose", "--output-format", "stream-json", "--input-format", "stream-json",
               "--include-partial-messages", "--permission-mode", self.permission_mode,
               "--append-system-prompt", SYSTEM_PROMPT]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        self.proc = subprocess.Popen(cmd, cwd=str(self.repo), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._pump, daemon=True).start()
        log(f"claude iniciado ({'reanudado ' + self.session_id if self.session_id else 'sesion nueva'})")

    def _pump(self):
        proc = self.proc
        for line in proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def turn(self, text, emit, cancelled):
        if self.proc is None or self.proc.poll() is not None:
            self._spawn()
        message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        streamed = 0
        while True:
            if cancelled():
                log("cancelacion: se termina el proceso y se reanudara la sesion")
                self.proc.kill()
                self.proc = None
                emit(elp1.REPLY, "(interrumpido)", True)
                return
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                emit(elp1.ERROR, "claude termino inesperadamente", True)
                self.proc = None
                return
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                self.session_id = event.get("session_id", self.session_id)
                self.name = "claude-code " + str(event.get("model", ""))
            elif kind == "stream_event":
                inner = event.get("event", {})
                delta = inner.get("delta", {})
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    piece = delta.get("text", "")
                    if piece:
                        streamed += len(piece)
                        emit(elp1.REPLY, piece, False)
            elif kind == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        emit(elp1.STATUS, describe_tool(block), False)
                    elif block.get("type") == "text" and streamed == 0 and block.get("text"):
                        emit(elp1.REPLY, block["text"], False)
            elif kind == "result":
                if event.get("session_id"):
                    self.session_id = event["session_id"]
                if event.get("is_error") and streamed == 0:
                    emit(elp1.REPLY, "error: " + str(event.get("result", ""))[:800], True)
                else:
                    emit(elp1.REPLY, "", True)
                return

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def describe_tool(block) -> str:
    name = block.get("name", "herramienta")
    args = block.get("input", {}) or {}
    target = args.get("file_path") or args.get("command") or args.get("pattern") or args.get("description") or ""
    target = str(target).replace("\n", " ")
    if len(target) > 60:
        target = target[:57] + "..."
    verbs = {"Read": "lee", "Edit": "edita", "Write": "escribe", "Bash": "ejecuta", "Grep": "busca", "Glob": "busca"}
    return f"{verbs.get(name, 'usa ' + name)} {target}".strip()


class CodexAgent(Agent):
    """Codex CLI: un proceso por turno (`codex exec --json`), reanudando la ultima sesion."""

    def __init__(self, repo: Path, executable: str | None):
        self.repo = repo
        self.exe = executable or find_executable("codex", codex_candidates())
        if not self.exe:
            raise SystemExit("no se encontro codex.exe; instala Codex o pasa --executable")
        self.name = "codex"
        self.started = False
        self.proc: subprocess.Popen | None = None

    def turn(self, text, emit, cancelled):
        cmd = [self.exe, "exec"]
        if self.started:
            cmd += ["resume", "--last"]
        cmd += ["--json", "-C", str(self.repo), "--skip-git-repo-check", text]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1)
        self.started = True
        answered = False
        for line in self.proc.stdout:
            if cancelled():
                self.proc.kill()
                emit(elp1.REPLY, "(interrumpido)", True)
                return
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {}) or {}
            if event.get("type") == "item.started" and item.get("type") == "command_execution":
                emit(elp1.STATUS, "ejecuta " + str(item.get("command", ""))[:60], False)
            elif event.get("type") == "item.completed" and item.get("type") == "agent_message":
                emit(elp1.REPLY, item.get("text", ""), False)
                answered = True
        self.proc.wait()
        emit(elp1.REPLY, "" if answered else "codex no devolvio respuesta", True)


def make_agent(args) -> Agent:
    repo = Path(args.repo).resolve()
    if args.agent == "echo":
        return EchoAgent()
    if args.agent == "claude":
        return ClaudeAgent(repo, args.permission_mode, args.executable)
    if args.agent == "codex":
        return CodexAgent(repo, args.executable)
    raise SystemExit(f"agente desconocido: {args.agent}")


def serve(args):
    token = args.token.encode("utf-8")[:16].ljust(16, b"\0")
    agent = make_agent(args)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    log(f"escuchando en {args.bind}:{args.port} agente={args.agent}")
    if args.ready_file:
        Path(args.ready_file).write_text("ready\n")
    sessions = 0
    try:
        while args.max_sessions == 0 or sessions < args.max_sessions:
            sock.settimeout(None)
            raw, peer = sock.recvfrom(2048)
            parsed = elp1.unpack(raw)
            if parsed is None or parsed[0] != elp1.HELLO:
                continue
            kind, flags, session, seq, ack, body = parsed
            if session == 0 or body[:16] != token:
                log(f"HELLO rechazado de {peer}: token invalido")
                sock.sendto(elp1.pack(elp1.ERROR, session, 1, seq, b"token invalido", True), peer)
                continue
            client = body[16:].decode("utf-8", "replace")
            link = elp1.Link(sock, peer, session, log)
            link.received = seq
            sock.sendto(elp1.pack(elp1.ACK, session, 0, seq), peer)
            if not link.send(elp1.HELLO_OK, agent.name.encode("utf-8")):
                continue
            log(f"sesion {session:#x} con {client!r} desde {peer}")
            sessions += 1
            run_session(link, agent)
            log(f"sesion {session:#x} cerrada")
    finally:
        agent.close()
        sock.close()


def run_session(link: elp1.Link, agent: Agent):
    buffer = bytearray()
    while link.alive:
        item = link.recv(30.0)
        if item is None:
            if not link.send(elp1.PING):
                return
            continue
        kind, flags, body = item
        if kind == elp1.PING:
            link.send(elp1.PONG)
        elif kind == elp1.USER:
            buffer += body
            if flags & elp1.FLAG_LAST:
                text = buffer.decode("utf-8", "replace")
                buffer = bytearray()
                log(f"usuario: {text!r}")
                run_turn(link, agent, text)
        elif kind == elp1.CANCEL:
            pass  # sin turno en curso: nada que cancelar


def run_turn(link: elp1.Link, agent: Agent, text: str):
    cancelled = threading.Event()
    outbox: queue.Queue = queue.Queue()

    def emit(kind, chunk, last):
        outbox.put((kind, chunk, last))

    worker = threading.Thread(target=agent.turn, args=(text, emit, cancelled.is_set), daemon=True)
    worker.start()
    finished = False
    while not finished and link.alive:
        # Atender datagramas del cliente entre trozos (cancelacion, ping).
        item = link.recv(0.05)
        if item is not None:
            kind, flags, body = item
            if kind == elp1.CANCEL:
                log("cancelacion pedida por el usuario")
                cancelled.set()
            elif kind == elp1.PING:
                link.send(elp1.PONG)
        try:
            kind, chunk, last = outbox.get(timeout=0.05)
        except queue.Empty:
            continue
        if kind == elp1.STATUS:
            link.send_text(elp1.STATUS, chunk[:200])
            continue
        if chunk:
            data = chunk.encode("utf-8")
            if last:
                link.send_text(elp1.REPLY, chunk)
            else:
                for start in range(0, len(data), elp1.BODY_MAX):
                    end = min(start + elp1.BODY_MAX, len(data))
                    while end < len(data) and (data[end] & 0xC0) == 0x80:
                        end -= 1
                    link.send(elp1.REPLY, data[start:end], last=False)
        elif last:
            link.send(elp1.REPLY, b"", last=True)
        if kind == elp1.ERROR:
            link.send_text(elp1.ERROR, chunk)
        finished = last


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", choices=["claude", "codex", "echo"], default="echo")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4040)
    parser.add_argument("--token", default="erlanos-pasarela-v1")
    parser.add_argument("--permission-mode", default="acceptEdits",
                        help="modo de permisos de Claude Code (acceptEdits, plan, default)")
    parser.add_argument("--executable", help="ruta del binario del agente si no esta en PATH")
    parser.add_argument("--max-sessions", type=int, default=0, help="0 = sin limite")
    parser.add_argument("--ready-file", help="archivo que se crea cuando el socket esta escuchando")
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
