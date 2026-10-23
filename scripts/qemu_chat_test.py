#!/usr/bin/env python3
"""Run isolated real-model chat tests and fail on timeout/kernel failure.

Usage: python scripts/qemu_chat_test.py hola [timeout_s]
       python scripts/qemu_chat_test.py --regression --timeout 1500
       python scripts/qemu_chat_test.py --semantic --accel whpx --model models/qwen3.5-2b-q8_0.gguf

Regression checks ESC cancellation, preserved queued input, a Spanish greeting,
identity, thanks, and the frontend token limit. The semantic suite also checks
arithmetic, counting, user memory and Python code. Transcript, screenshots and
timings stay under build/m3-test/<run>/, without touching another VM.
"""
import argparse
import json
import re
import shutil
import sys
import time
import unicodedata

from qemu_m4_test import DONE, QemuSession, TestFailure, common_arguments, keyseq, receipts


CHAT_BEGIN = "[model] chat: begin"
CHAT_DONE = re.compile(r"\[model\] chat: done reason=(eos|limit|cancel|reject|unloaded) tokens=(\d+)(?:\r?\n|$)")
CHAT_ANSWER = re.compile(r"\[model\] chat: answer begin\r?\n(.*?)\[model\] chat: answer end", re.DOTALL)


def parse_chat(text):
    terminal = CHAT_DONE.findall(text)
    if len(terminal) != 1:
        raise TestFailure(f"Expected one chat terminal marker, got {terminal}")
    reason, count = terminal[0]
    answer = CHAT_ANSWER.search(text)
    if answer is None:
        raise TestFailure("Missing serial answer delimiters")
    content = answer.group(1)
    if content.startswith("[model] "):
        content = content[len("[model] "):]
    return {"answer": content.strip(), "reason": reason, "tokens": int(count)}


def save_frame(vm, label):
    vm.capture()
    source = vm.result.get("screenshot")
    if source:
        from pathlib import Path
        destination = vm.directory / (label + Path(source).suffix)
        shutil.copy2(source, destination)
        return str(destination)
    return None


def collect_chat(vm, before, question, started, timeout=None):
    done_count = before.count(DONE)
    after = vm.wait_for(lambda text: text.count(DONE) > done_count,
                        f"chat response to {question!r}", timeout or vm.args.command_timeout)
    new = after[len(before):]
    record = {"input": question, "elapsed_s": round(time.monotonic() - started, 3),
              "serial": new, **parse_chat(new)}
    vm.result["commands"].append(record)
    if record["tokens"] > vm.args.max_tokens:
        raise TestFailure(f"Reply exceeded {vm.args.max_tokens}-token budget: {record['tokens']}")
    record["screenshot"] = save_frame(vm, f"turn-{len(vm.result['commands']):02d}")
    print(f"[test] chat reason={record['reason']} tokens={record['tokens']} elapsed={record['elapsed_s']}s", flush=True)
    print(f"[test] answer: {record['answer']}", flush=True)
    if getattr(vm.args, "expect_avx2", False) and "[model] matmul: AVX2" not in vm.log():
        raise TestFailure("Guest did not report the expected AVX2 matmul backend")
    return record


def completed_reply(record):
    if record["reason"] not in ("eos", "limit") or not record["answer"] or record["tokens"] == 0:
        raise TestFailure(f"Expected a nonempty model response, got {record}")


def normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(char))


def ask(vm, question):
    before = vm.log()
    started = time.monotonic()
    print(f"[test] asking: {question}", flush=True)
    if "\n" in question:
        vm.type_multiline(question)
    else:
        vm.type_line(question)
    reply = collect_chat(vm, before, question, started)
    completed_reply(reply)
    return reply


def cancel_and_preserve(vm):
    # Queue a system command while the model is busy, then cancel.
    # Input cancellation must remove Escape only, preserving ordinary keys.
    question = "Explica como funciona un sistema operativo."
    before = vm.log()
    started = time.monotonic()
    vm.type_line(question)
    vm.wait_for(lambda text: text.count(CHAT_BEGIN) > before.count(CHAT_BEGIN),
                "chat inference to start", 15)
    vm.type_line("/estado")
    cancelled_at = time.monotonic()
    vm.send_key("esc")
    cancelled = collect_chat(vm, before, question, started, vm.args.cancel_timeout)
    cancelled["cancel_latency_s"] = round(started + cancelled["elapsed_s"] - cancelled_at, 3)
    if cancelled["reason"] != "cancel":
        raise TestFailure(f"Escape did not cancel active inference: {cancelled}")
    if cancelled["cancel_latency_s"] > vm.args.cancel_timeout:
        raise TestFailure(f"Escape cancellation exceeded {vm.args.cancel_timeout}s")

    # The entire /estado command was queued during inference. It must execute
    # after cancellation without retyping any characters or pressing Enter.
    after = vm.wait_for(lambda text: text.count(DONE) >= before.count(DONE) + 2,
                        "queued /estado after Escape", 15)
    queued = receipts(after[len(before):])
    if len(queued) != 1 or queued[0]["op"] != 1 or queued[0]["status"] != 0:
        raise TestFailure(f"Queued /estado was lost or failed after cancellation: {queued}")
    vm.result["queued_command_receipt"] = queued[0]
    vm.result["queued_input_preserved"] = True


def regression(vm):
    cancel_and_preserve(vm)
    greeting = ask(vm, "hola")
    if not re.search(r"\b(hola|buenos|buenas|saludos|bienvenido)\b", normalized(greeting["answer"])):
        raise TestFailure(f"Greeting did not answer hola in Spanish: {greeting['answer']!r}")

    identity = ask(vm, "Como te llamas?")
    if "erlan" not in normalized(identity["answer"]):
        raise TestFailure(f"Second turn did not retain Erlan identity: {identity['answer']!r}")
    thanks = ask(vm, "gracias")
    if not re.search(r"\b(de nada|con gusto|un placer|a ti|por nada|encantad[oa]|para ayudarte)\b",
                     normalized(thanks["answer"])):
        raise TestFailure(f"Third turn did not answer thanks in Spanish: {thanks['answer']!r}")
    budget = ask(vm, "Escribe los numeros del uno al cien sin parar.")
    vm.result["limit_reached"] = budget["reason"] == "limit"
    if budget["reason"] == "limit" and budget["tokens"] != vm.args.max_tokens:
        raise TestFailure(f"Limit termination at an unexpected token count: {budget['tokens']}")
    vm.result["queued_input_preserved"] = True
    vm.result["spanish_greeting_verified"] = True
    vm.result["second_turn_identity_verified"] = True
    vm.result["third_turn_courtesy_verified"] = True


def semantic(vm):
    from test_chat_semantics import evaluate
    questions = ["hola", "me llamo erlan, quien eres tu?", "como te llamas tu?",
                 "quien eres?", "wtf?", "Me llamo Lucia. Recuerda mi nombre.",
                 "Como me llamo?", "Cuanto es 2 + 2? Responde solo con el numero.",
                 "Cuenta del 1 al 5. Responde solo con esos cinco numeros.",
                 "Escribe solo codigo Python: una funcion suma(a, b) que devuelve a + b."]
    statuses = {"eos": 0, "limit": 1, "cancel": 2, "reject": 3, "unloaded": 4}
    records = []
    vm.result["semantic_context_policy"] = "Continuous guest conversation; no hidden context resets"
    for question in questions:
        reply = ask(vm, question)
        records.append({"user": question, "answer": reply["answer"],
                        "status": statuses[reply["reason"]], "tokens": reply["tokens"],
                        "elapsed_s": reply["elapsed_s"], "screenshot": reply["screenshot"]})
        # Keep a useful partial transcript even if a later turn panics or times out.
        (vm.directory / "semantic-transcript.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = evaluate(records)
    vm.result["semantic_checks"] = checks
    vm.result["semantic_passed"] = all(check["passed"] for check in checks)
    (vm.directory / "semantic-result.json").write_text(
        json.dumps({"passed": vm.result["semantic_passed"], "checks": checks,
                    "context_policy": vm.result["semantic_context_policy"]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cancel_and_preserve(vm)
    failures = [{"case": check["case"], "criteria": [name for name, passed in check["criteria"].items() if not passed]}
                for check in checks if not check["passed"]]
    if failures:
        raise TestFailure(f"Semantic checks failed: {failures}")


def coder(vm):
    from test_coder_host import QUERIES
    from test_chat_semantics import correct_sum_code
    records = []
    for query in QUERIES:
        reply = ask(vm, query)
        record = {"user": query, "host_query": query, **reply,
                  "passed": correct_sum_code(reply["answer"]) and reply["reason"] == "eos"
                            and 0 < reply["tokens"] <= 64}
        records.append(record)
        (vm.directory / "coder-result.json").write_text(json.dumps(
            {"passed": len(records) == 2 and all(row["passed"] for row in records),
             "generated_code_executed": False, "checks": records},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    vm.result["coder_checks"] = records
    vm.result["generated_code_executed"] = False
    cancel_and_preserve(vm)
    if not all(row["passed"] for row in records):
        raise TestFailure("Coder generation or repair did not produce the expected Python AST")


def smoke(vm):
    greeting = ask(vm, "hola")
    if not re.search(r"\b(hola|buenos|buenas|saludos)\b", normalized(greeting["answer"])):
        raise TestFailure("Optimized model greeting was not in Spanish")
    identity = ask(vm, "me llamo erlan, quien eres tu?")
    if "erlan" not in normalized(identity["answer"]) or identity["reason"] != "eos":
        raise TestFailure("Optimized model failed the identity turn")
    before_status = receipts(vm.command("/estado"))
    unavailable = vm.command("/ai Show system status")
    if receipts(unavailable) or CHAT_BEGIN in unavailable or "m4: plan=" in unavailable:
        raise TestFailure("Unsupported /ai produced inference or a system action")
    vm.result["unsupported_ai_screenshot"] = save_frame(vm, "ai-unavailable")
    cancel_and_preserve(vm)
    after_status = vm.result["queued_command_receipt"]
    if len(before_status) != 1 or any(before_status[0][key] != after_status[key] for key in ("mode", "revision")):
        raise TestFailure("Unsupported /ai or cancellation mutated system mode or revision")
    vm.result["optimized_smoke_passed"] = True
    vm.result["unsupported_ai_no_side_effects"] = True


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default="hola")
    parser.add_argument("timeout_s", nargs="?", type=int, help="Legacy positional timeout")
    common_arguments(parser)
    parser.set_defaults(timeout=900, command_timeout=600)
    suite = parser.add_mutually_exclusive_group()
    suite.add_argument("--regression", action="store_true", help="Test cancel, preserved keys, three Spanish turns and token budget")
    suite.add_argument("--semantic", action="store_true", help="Test ten real user turns (identity, memory, math, count and code), then Escape")
    suite.add_argument("--coder", action="store_true", help="Test code generation and bug repair by Python AST inspection, then Escape")
    suite.add_argument("--smoke", action="store_true", help="Check greeting, identity, unavailable /ai and Escape after optimizing the runtime")
    parser.add_argument("--max-tokens", type=int, default=64, help="Expected frontend reply budget (default: 64)")
    parser.add_argument("--cancel-timeout", type=float, default=10, help="Maximum Escape cancellation latency in seconds")
    parser.add_argument("--expect-avx2", action="store_true", help="Require the real guest matmul: AVX2 marker")
    args = parser.parse_args(argv)
    if args.timeout_s is not None:
        args.timeout = args.timeout_s
    if min(args.timeout, args.command_timeout, args.max_tokens, args.cancel_timeout) <= 0:
        parser.error("timeouts and max-tokens must be positive")
    if not args.question or len(args.question) > 240:
        parser.error("question must contain between 1 and 240 characters")
    try:
        keyseq(args.question)
    except ValueError as error:
        parser.error(str(error))
    vm = None
    failure = None
    try:
        vm = QemuSession(args, suite="m3")
        vm.start()
        vm.wait_for(lambda log: "[model] model: listo" in log and "[model] m4: ready" in log,
                    "model input readiness")
        vm.result["readiness_elapsed_s"] = round(time.monotonic() - vm.started, 3)
        vm.result["guest_readiness_elapsed_s"] = round(vm.result["readiness_elapsed_s"] -
            vm.result.get("qemu_started_elapsed_s", 0), 3)
        print(f"[test] model input ready after {vm.result['readiness_elapsed_s']}s", flush=True)
        if args.smoke:
            smoke(vm)
        elif args.coder:
            coder(vm)
        elif args.semantic:
            semantic(vm)
        elif args.regression:
            regression(vm)
        else:
            ask(vm, args.question)
        vm.check_health()
    except KeyboardInterrupt:
        failure = TestFailure("Interrupted by user")
    except Exception as error:
        failure = error
    if vm is not None:
        return vm.finish(failure)
    print(f"[test] FAIL: {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
