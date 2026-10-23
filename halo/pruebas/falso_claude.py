"""Doble de Claude Code para las pruebas del chat: habla el mismo protocolo de flujo JSON.

    python falso_claude.py -p --input-format stream-json --output-format stream-json ... --session-id ID
    python falso_claude.py auth status

Escribe una transcripción con el formato de ~/.claude/projects en la carpeta FALSO_CLAUDE_TRANSCRIPCIONES.
Frases que cambian su conducta: «permiso» pide permiso para Bash; «espera» tarda hasta que lo interrumpen;
«falla» termina con error.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path


def salida(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
        return 0
    opcion = lambda nombre: args[args.index(nombre) + 1] if nombre in args else None  # noqa: E731
    sesion = opcion("--session-id") or opcion("--resume") or str(uuid.uuid4())
    if "--fork-session" in args:
        sesion = str(uuid.uuid4())
    raiz = Path(os.environ.get("FALSO_CLAUDE_TRANSCRIPCIONES", Path.home() / ".claude" / "projects"))
    transcripcion = raiz / "proyecto-falso" / f"{sesion}.jsonl"
    transcripcion.parent.mkdir(parents=True, exist_ok=True)

    def anotar(tipo: str, contenido) -> None:
        with open(transcripcion, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": tipo, "message": {"role": "user" if tipo == "user" else "assistant", "content": contenido},
                                 "uuid": uuid.uuid4().hex, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "sessionId": sesion}) + "\n")

    salida({"type": "system", "subtype": "init", "session_id": sesion, "model": opcion("--model") or "falso",
            "permissionMode": opcion("--permission-mode") or "default", "cwd": os.getcwd()})
    entradas: queue.Queue = queue.Queue()

    def leer_stdin() -> None:
        for linea in sys.stdin:
            try:
                entradas.put(json.loads(linea))
            except ValueError:
                pass
        entradas.put(None)

    threading.Thread(target=leer_stdin, daemon=True).start()
    mensaje = entradas.get()
    if not mensaje:
        return 1
    texto = "".join(b.get("text", "") for b in (mensaje.get("message") or {}).get("content", []) if isinstance(b, dict))
    anotar("user", texto)

    def decir(frase: str) -> None:
        for trozo in (frase[i:i + 6] for i in range(0, len(frase), 6)):
            salida({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": trozo}}})
            time.sleep(0.01)
        salida({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": frase}]}, "session_id": sesion})
        anotar("assistant", [{"type": "text", "text": frase}])

    if "falla" in texto:
        salida({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "fallo simulado", "session_id": sesion})
        return 1
    if "espera" in texto:
        salida({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Pensando"}}})
        while True:
            try:
                dato = entradas.get(timeout=10)
            except queue.Empty:
                dato = None
            if dato is None:
                salida({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "sin interrupción", "session_id": sesion})
                return 1
            if dato.get("type") == "control_request" and (dato.get("request") or {}).get("subtype") == "interrupt":
                salida({"type": "control_response", "response": {"subtype": "success", "request_id": dato.get("request_id")}})
                salida({"type": "result", "subtype": "success", "is_error": False, "result": "interrumpido", "num_turns": 1, "session_id": sesion})
                return 0
    if "permiso" in texto:
        rid = uuid.uuid4().hex
        entrada = {"command": "echo hola", "description": "Saludar"}
        salida({"type": "control_request", "request_id": rid,
                "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": entrada, "tool_use_id": "toolu_1"}})
        while True:
            dato = entradas.get()
            if dato is None:
                return 1
            if dato.get("type") == "control_response" and (dato.get("response") or {}).get("request_id") == rid:
                break
        respuesta = ((dato.get("response") or {}).get("response") or {})
        if respuesta.get("behavior") == "allow":
            salida({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": entrada}]}, "session_id": sesion})
            anotar("assistant", [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": entrada}])
            salida({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "hola"}]}, "session_id": sesion})
            anotar("user", [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "hola"}])
            decir("Listo: la orden imprimió «hola».")
        else:
            decir("Entendido, no ejecuto la orden.")
    else:
        decir("Hola desde el doble de Claude Code." + (" Retomo la conversación." if "--resume" in args else ""))
    salida({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "num_turns": 1, "duration_ms": 5,
            "total_cost_usd": 0.0, "session_id": sesion})
    return 0


if __name__ == "__main__":
    sys.exit(main())
