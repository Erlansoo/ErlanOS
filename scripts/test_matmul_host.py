#!/usr/bin/env python3
"""Numeric Q8 regression and benchmark; optional real-GGUF logit replay."""
import argparse
from array import array
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="GGUF path for full-forward equivalence/cancellation checks")
    parser.add_argument("--build", default="build/matmul-host")
    parser.add_argument("--float-baseline", action="store_true", help="test the previous float4 path instead of production Q20")
    parser.add_argument("--portable", action="store_true", help="force the baseline ISA without AVX2 dispatch")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = (root / args.build).resolve()
    gen = out / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("clang")
    if not compiler and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LLVM/bin/clang.exe"
        if candidate.is_file(): compiler = str(candidate)
    if not compiler: parser.error("clang required")

    def run(command):
        subprocess.run([str(p) for p in command], cwd=root, check=True)

    llama_source = (root / "services/model/llama.e").read_text(encoding="utf-8")
    if args.float_baseline:
        start = llama_source.index("fn matmul(")
        end = llama_source.index("// Cuantización", start)
        llama_source = llama_source[:start] + "fn matmul(out: []f32, xin: []f32, w: u64, wtype: u32, rows: u64, cols: u64) { matmul_float(out, xin, w, wtype, rows, cols); }\n\n" + llama_source[end:]
    selected_source = out / "llama_selected.e"
    selected_source.write_text(llama_source, encoding="utf-8")
    print(f"MATMUL_PATH {'float4-test-baseline' if args.float_baseline else 'q20-production'}", flush=True)
    for module in ("arena", "mathx", "gguf", "qwen35", "matmul_fast", "llama"):
        source = selected_source if module == "llama" else f"services/model/{module}.e"
        run([sys.executable, "erlan/erlancompiler.py", source, "-o", gen / f"{module}.c",
             "--header", gen / f"{module}.h"])
    run([sys.executable, "erlan/erlancompiler.py", "tests/model/matmul_reference.e", "-o", gen / "matmul_reference.c",
         "--header", gen / "matmul_reference.h"])
    flags = [compiler, "-std=c11", "-O3", "-g", "-Wall", "-Wno-unused-const-variable", "-Wno-unused-parameter",
             "-Wno-unused-variable", "-Wno-parentheses-equality", "-Itests/model/stubs", f"-I{gen}", "-Iservices/model", "-Ierlan/runtime"]
    common = ["tests/model/numeric.c", *[gen / f"{m}.c" for m in ("arena", "mathx", "gguf", "qwen35", "matmul_reference")]]
    fast_object = out / "matmul_fast.o"
    run([*flags, "-mavx2", "-mno-fma", "-c", gen / "matmul_fast.c", "-o", fast_object])
    common.append(fast_object)
    if args.portable: flags.append("-DERLAN_MATMUL_FORCE_PORTABLE")
    executable = out / ("numeric.exe" if os.name == "nt" else "numeric")
    run([*flags, *common, gen / "llama.c", "-o", executable])
    run([executable])
    if args.model:
        # Replace only the optimized matmul with the preserved M3 reference;
        # cancellation and every other forward operation remain identical.
        original = llama_source
        reference = (root / "tests/model/matmul_reference.e").read_text(encoding="utf-8")
        body = reference[reference.index("fn reference_matmul("):].replace("fn reference_matmul(", "fn matmul(", 1)
        start, end = original.index("fn matmul("), original.index("fn dequant_row(")
        baseline = out / "llama_baseline.e"
        baseline.write_text(original[:start] + body + "\n" + original[end:], encoding="utf-8")
        run([sys.executable, "erlan/erlancompiler.py", baseline, "-o", gen / "llama_baseline.c"])
        reference_exe = out / ("baseline.exe" if os.name == "nt" else "baseline")
        run([*flags, *common, gen / "llama_baseline.c", "-o", reference_exe])
        model = (root / args.model).resolve()
        old_dump, new_dump = out / "reference-logits.bin", out / "optimized-logits.bin"
        run([reference_exe, model, old_dump]); run([executable, model, new_dump])
        if not args.portable and not args.float_baseline:
            portable_exe = out / ("portable.exe" if os.name == "nt" else "portable")
            run([*flags, "-DERLAN_MATMUL_FORCE_PORTABLE", *common, gen / "llama.c", "-o", portable_exe])
            run([portable_exe])
            portable_dump = out / "portable-logits.bin"
            run([portable_exe, model, portable_dump])
            assert portable_dump.read_bytes() == new_dump.read_bytes(), "AVX2/portable logit difference"
            print("AVX2_PORTABLE_EQUIVALENCE PASS all_logits_bit_exact=true")
        old, new = array("f", old_dump.read_bytes()), array("f", new_dump.read_bytes())
        assert len(old) == len(new) and len(old) > 0
        errors = [abs(float(a) - b) for a, b in zip(old, new)]
        max_error = max(errors); rms = math.sqrt(sum(e * e for e in errors) / len(errors))
        width = len(old) // 8
        argmax_matches = all(max(range(width), key=lambda i: old[p * width + i]) ==
                             max(range(width), key=lambda i: new[p * width + i]) for p in range(8))
        assert max_error < 0.002 and rms < 0.0002 and argmax_matches, (max_error, rms, argmax_matches)
        print(f"LOGIT_EQUIVALENCE PASS values={len(old)} max_abs={max_error:.8g} rms={rms:.8g} argmax=8/8")
        prefill_dump = out / "prefill-logits.bin"
        run([executable, model, prefill_dump, "prefill"])
        assert prefill_dump.read_bytes() == new_dump.read_bytes()[-width * 4:]
        print(f"PREFILL_EQUIVALENCE PASS final_logits={width} bit_exact=true cancellation=PASS")


if __name__ == "__main__": main()
