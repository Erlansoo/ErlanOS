#!/usr/bin/env python3
"""Evaluate native model answers, independently of cancellation and token limits.

This is a small regression suite, not an assessment of general intelligence.
It includes the user's failed transcript without putting those answers in the
model prompt. Coding is checked by AST inspection; generated code is not run.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        c for c in plain if not unicodedata.combining(c))))


def correct_sum_code(text: str) -> bool:
    source = text.strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            return False
        source = "\n".join(lines[1:-1])
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    fn = tree.body[0]
    if (fn.name != "suma" or [a.arg for a in fn.args.args] != ["a", "b"]
            or fn.decorator_list or fn.args.defaults or fn.args.vararg
            or fn.args.kwarg or fn.args.kwonlyargs or len(fn.body) != 1):
        return False
    result = fn.body[0]
    if not isinstance(result, ast.Return) or not isinstance(result.value, ast.BinOp):
        return False
    expr = result.value
    return (isinstance(expr.op, ast.Add) and isinstance(expr.left, ast.Name)
            and isinstance(expr.right, ast.Name)
            and {expr.left.id, expr.right.id} == {"a", "b"})


def evaluate(records: list[dict]) -> list[dict]:
    checks = []
    if len(records) != 10:
        return [{"case": "complete_transcript", "passed": False,
                 "detail": f"expected 10 turns, got {len(records)}"}]
    for index, row in enumerate(records):
        answer = row["answer"]
        norm = normalized(answer)
        criteria = {
            "nonempty": bool(norm),
            "completed": row["status"] == 0,
            "bounded": 0 < row["tokens"] <= 64,
            "no_reasoning_tags": "<think>" not in answer and "</think>" not in answer,
        }
        if index != 0:  # A natural greeting can be the same word as the user's.
            criteria["not_echo"] = norm != normalized(row["user"])
        if index == 0:
            criteria["greeting"] = bool(re.search(r"\b(hola|saludos|buenos|buenas)\b", norm))
        elif index in (1, 2, 3):
            criteria["assistant_identity"] = "erlan" in norm and (bool(re.search(
                r"\b(soy|llamo|asistente|inteligencia)\b", norm)) or "nombre es" in norm)
        elif index == 4:
            criteria["responds_to_confusion"] = len(norm.split()) >= 3
        elif index == 6:
            criteria["remembers_user_name"] = "lucia" in norm.split()
        elif index == 7:
            criteria["correct_arithmetic"] = norm == "4"
        elif index == 8:
            criteria["correct_count"] = re.findall(r"\d+", answer) == ["1", "2", "3", "4", "5"]
        elif index == 9:
            criteria["correct_python_function"] = correct_sum_code(answer)
        checks.append({"case": index, **row, "criteria": criteria,
                       "passed": all(criteria.values())})
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("build/chat-host/chat_test.exe"))
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("build/chat-host/semantic-result.json"))
    parser.add_argument("--transcript", type=Path,
                        help="evaluate an existing native stdout transcript without running the model")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    code = 0
    if args.transcript:
        raw = args.transcript.read_text(encoding="utf-8", errors="replace")
    else:
        output_path = args.output.with_suffix(".stdout.log")
        error_path = args.output.with_suffix(".stderr.log")
        with output_path.open("w", encoding="utf-8") as output, error_path.open("w", encoding="utf-8") as errors:
            process = subprocess.Popen([str(args.exe.resolve()), str(args.model.resolve()), "--semantic"],
                stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    if line.startswith("[host-json] "):
                        row = json.loads(line[len("[host-json] "):])
                        print(f"USER: {row['user']}\nANSWER: {row['answer']}", flush=True)
                code = process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
        raw = output_path.read_text(encoding="utf-8", errors="replace")
    records = [json.loads(line[len("[host-json] "):]) for line in raw.splitlines()
               if line.startswith("[host-json] ")]
    checks = evaluate(records)
    passed = code == 0 and all(check["passed"] for check in checks)
    report = {"passed": passed, "model": str(args.model.resolve()), "native_exit": code,
              "elapsed_seconds": round(time.monotonic() - started, 3), "checks": checks,
              "scope": "Ten small semantic regression cases; control suite is separate."}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for check in checks:
        failed = [name for name, success in check.get("criteria", {}).items() if not success]
        print(f"case={check['case']} {'PASS' if check['passed'] else 'FAIL'} {','.join(failed)}")
    print(f"CHAT_SEMANTIC_TEST {'PASS' if passed else 'FAIL'} result={args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
