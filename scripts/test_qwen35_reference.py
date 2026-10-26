#!/usr/bin/env python3
"""Replay exact IDs and compare Qwen3.5 full-model predictions with llama.cpp.

The checked-in fixture records llama.cpp b10887 with F32 KV and Q8_0 weights.
Its activation dot product uses Q8; Erlan uses Q20. Thus log probabilities are
compared as normalized probabilities, with exact greedy token agreement, rather
than pretending the two quantized arithmetic paths are bit-identical.
"""
from array import array
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default="models/qwen3.5-2b-q8_0.gguf")
    p.add_argument("--reference", default="tests/model/qwen35_reference.json")
    p.add_argument("--build", default="build/qwen35-reference")
    p.add_argument("--cases", help="comma-separated fixture indices; default all")
    p.add_argument("--portable", action="store_true")
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    model = (root / args.model).resolve()
    reference = json.loads((root / args.reference).read_text(encoding="utf-8"))
    with model.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    assert digest == reference["model_sha256"], "reference belongs to a different GGUF"
    out = (root / args.build).resolve(); gen = out / "gen"; gen.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang") or "C:/Program Files/LLVM/bin/clang.exe"
    modules = ("umath", "arena", "mathx", "gguf", "qwen35", "matmul_fast", "llama")
    for module in modules:
        subprocess.run([sys.executable, "erlan/erlancompiler.py", f"services/model/{module}.e", "-o",
                        str(gen / f"{module}.c"), "--header", str(gen / f"{module}.h"), "-Iservices/model", "-Iservices/lib", "-Imind"], cwd=root, check=True)
    exe = out / ("probe.exe" if os.name == "nt" else "probe")
    fast_object = out / "matmul_fast.o"
    subprocess.run([compiler, "-std=c11", "-O3", "-mavx2", "-mno-fma", "-Wno-parentheses-equality",
                    f"-I{gen}", "-Ierlan/runtime", "-c", str(gen / "matmul_fast.c"), "-o", str(fast_object)], cwd=root, check=True)
    portable_flags = ["-DERLAN_MATMUL_FORCE_PORTABLE"] if args.portable else []
    subprocess.run([compiler, "-std=c11", "-O3", "-Wno-parentheses-equality", "-Itests/model/stubs", *portable_flags,
                    f"-I{gen}", "-Iservices/model", "-Ierlan/runtime", "tests/model/qwen35_probe.c",
                    *[str(gen / f"{m}.c") for m in modules if m != "matmul_fast"], str(fast_object), "-o", str(exe)], cwd=root, check=True)
    cases = list(range(len(reference["cases"]))) if args.cases is None else [int(c) for c in args.cases.split(",")]
    report = {"model_sha256": digest, "reference_runtime": reference["reference_runtime"], "cases": []}
    for index in cases:
        case = reference["cases"][index]
        ids, probs = case["prompt_ids"], case["predictions"]
        tokens = ids + [step["id"] for step in probs[:-1]]
        ids_path, dump_path = out / f"case-{index}.ids", out / f"case-{index}.bin"
        ids_path.write_text(str(len(ids) - 1) + "\n" + " ".join(map(str, tokens)), encoding="ascii")
        subprocess.run([str(exe), str(model), str(ids_path), str(dump_path)], cwd=root, check=True)
        logits = array("f", dump_path.read_bytes())
        vocab = reference["vocab_size"]
        assert len(logits) == len(probs) * vocab
        item = {"name": case["name"], "prompt_tokens": len(ids), "steps": []}
        for step, expected in enumerate(probs):
            values = logits[step * vocab:(step + 1) * vocab]
            top = max(range(vocab), key=values.__getitem__)
            maximum = values[top]
            log_z = maximum + math.log(sum(math.exp(float(x) - maximum) for x in values))
            errors = [abs(math.exp(values[t["id"]] - log_z) - math.exp(t["logprob"]))
                      for t in expected["top_logprobs"]]
            log_errors = [abs(values[t["id"]] - log_z - t["logprob"]) for t in expected["top_logprobs"]]
            row = {"top1": top, "expected_top1": expected["id"], "top20_max_probability_error": max(errors),
                   "top20_l1_probability_error": sum(errors), "top20_max_logprob_error": max(log_errors)}
            item["steps"].append(row)
            # Independent quantized reference gate; strict Q20/scalar rounding
            # gates remain separately enforced by test_matmul_host.py.
            assert top == expected["id"] and max(errors) < 0.03 and sum(errors) < 0.1, row
        report["cases"].append(item)
        (out / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"QWEN35_REFERENCE PASS {case['name']} top1={len(probs)}/{len(probs)} "
              f"max_probability_error={max(s['top20_max_probability_error'] for s in item['steps']):.8g}", flush=True)


if __name__ == "__main__": main()
