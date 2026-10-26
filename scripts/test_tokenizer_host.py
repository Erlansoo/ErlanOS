#!/usr/bin/env python3
"""Verify bounded ASCII SmolLM pre-tokenizer parity using the local GGUF."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="models/smollm2-135m-instruct-q8_0.gguf")
    parser.add_argument("--build", default="build/tokenizer-host")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = (root / args.build).resolve(); out.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang")
    if not compiler and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LLVM/bin/clang.exe"
        if candidate.is_file(): compiler = str(candidate)
    if not compiler: parser.error("clang required")

    def run(command): subprocess.run([str(p) for p in command], cwd=root, check=True)

    for module in ("umath", "arena", "gguf", "unicode", "tokenizer"):
        run([sys.executable, "erlan/erlancompiler.py", f"services/model/{module}.e", "-o", out / f"{module}.c",
             "--header", out / f"{module}.h", "-Iservices/model", "-Iservices/lib", "-Imind"])
    executable = out / ("tokenizer.exe" if os.name == "nt" else "tokenizer")
    run([compiler, "-std=c11", "-O2", "-Wall", "-Wno-unused-const-variable", "-Wno-unused-parameter",
         "-Wno-unused-variable", "-Wno-parentheses-equality", "-Itests/model/stubs", f"-I{out}", "-Ierlan/runtime",
         "tests/model/tokenizer.c", *[out / f"{m}.c" for m in ("umath", "arena", "gguf", "unicode", "tokenizer")], "-o", executable])
    run([executable, (root / args.model).resolve()])


if __name__ == "__main__": main()
