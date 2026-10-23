#!/usr/bin/env python3
"""Check native code generation and repair; optionally compare offline llama.cpp.

The returned Python is inspected as an AST and never executed. This checks two
small tasks, not broad coding capability or permission to run generated code.
"""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from test_chat_semantics import correct_sum_code

def _system_prompt():
    """Mismo mensaje de sistema que el runtime: se extrae de services/model/chat.e (fn chat_system)."""
    src = (Path(__file__).resolve().parents[1] / "services/model/chat.e").read_text(encoding="utf-8")
    body = src.split("fn chat_system(", 1)[1].split("fn chat_prompt(", 1)[0]
    marker = "chat_text(n, " + chr(34)
    parts = []
    for line in body.splitlines():
        if marker not in line:
            continue
        text = line.split(marker, 1)[1]
        text = text[: text.rfind(chr(34))]
        # los trozos con secuencias de escape son delimitadores de rol, no prompt
        if chr(92) in text:
            continue
        parts.append(text)
    return "".join(parts)


SYSTEM = _system_prompt()
QUERIES = [
    "Escribe solo codigo Python: una funcion suma(a, b) que devuelve a + b.",
    "Corrige este codigo Python para que sume. Responde solo con codigo:\ndef suma(a, b):\n    return a - b",
]


def api(port, path, data=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
        None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def reference(exe, model, output):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with output.with_suffix(".reference.log").open("wb") as log:
        process = subprocess.Popen([str(exe.resolve()), "-m", str(model.resolve()), "--host", "127.0.0.1",
            "--port", str(port), "-c", "512", "-t", "2", "-ngl", "0", "--parallel", "1",
            "-fa", "off", "-ctk", "f32", "-ctv", "f32", "--reasoning", "off"],
            stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 120
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"reference server exited: {process.returncode}")
                try:
                    if api(port, "/health").get("status") == "ok":
                        break
                except (OSError, urllib.error.HTTPError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("reference server did not become ready")
                time.sleep(0.25)
            history = f"<|im_start|>system\n{SYSTEM}<|im_end|>\n"
            rows = []
            for query in QUERIES:
                history += f"<|im_start|>user\n{query}<|im_end|>\n<|im_start|>assistant\n"
                result = api(port, "/completion", {"prompt": history, "n_predict": 64,
                    "temperature": 0, "seed": 0, "repeat_penalty": 1.0, "cache_prompt": True})
                answer = result["content"]
                rows.append({"user": query, "answer": answer, "tokens": result["tokens_predicted"],
                    "stopped_eos": result.get("stop_type") == "eos" or result.get("stopped_eos", False),
                    "timings": result.get("timings"), "passed": correct_sum_code(answer)})
                history += answer + "<|im_end|>\n"
            return rows
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("build/qwen-coder/chat-host/chat_test.exe"))
    parser.add_argument("--model", type=Path, default=Path("models/qwen2.5-coder-1.5b-instruct-q8_0.gguf"))
    comparison = parser.add_mutually_exclusive_group()
    comparison.add_argument("--reference", type=Path)
    comparison.add_argument("--native-only", action="store_true", help="run only the native implementation")
    parser.add_argument("--expect-avx2", action="store_true", help="also require the runtime AVX2 dispatch log")
    parser.add_argument("--output", type=Path, default=Path("build/qwen-coder/code-result.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = subprocess.run([str(args.exe.resolve()), str(args.model.resolve()), *QUERIES],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    args.output.with_suffix(".native.stdout.log").write_text(result.stdout, encoding="utf-8")
    args.output.with_suffix(".native.stderr.log").write_text(result.stderr, encoding="utf-8")
    rows = [json.loads(line[len("[host-json] "):]) for line in result.stdout.splitlines()
            if line.startswith("[host-json] ")]
    metrics = re.findall(r"\[(\d+) tokens, (\d+) ms\]", result.stdout)
    for index, row in enumerate(rows):
        row["passed"] = correct_sum_code(row["answer"]) and row["status"] == 0 and 0 < row["tokens"] <= 64
        if index < len(metrics):
            row["reported_milliseconds"] = int(metrics[index][1])
    dispatch = "unknown"
    if "[model] matmul: AVX2" in result.stderr:
        dispatch = "avx2"
    elif "[model] matmul: portable" in result.stderr:
        dispatch = "portable"
    report = {"model": str(args.model.resolve()), "native_exit": result.returncode, "native": rows,
              "matmul_dispatch": dispatch, "generated_code_executed": False,
              "elapsed_native_seconds": round(time.monotonic() - started, 3)}
    # Preserve evidence even if the independent reference process fails later.
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.reference:
        report["reference"] = reference(args.reference, args.model, args.output)
        for native, other in zip(rows, report["reference"]):
            native["exact_reference_text"] = native["answer"] == other["answer"]
    report["passed"] = (result.returncode == 0 and len(rows) == 2 and all(row["passed"] for row in rows)
                        and (not args.expect_avx2 or dispatch == "avx2")
                        and all(row["passed"] for row in report.get("reference", [])))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
