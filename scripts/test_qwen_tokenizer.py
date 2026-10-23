#!/usr/bin/env python3
"""Check native tokenizer IDs/UTF-8 roundtrips against pinned llama.cpp fixtures."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=Path('models/qwen3.5-2b-q8_0.gguf'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = root / 'build/qwen-tokenizer'; out.mkdir(parents=True, exist_ok=True)
    cc = shutil.which('clang')
    if not cc and os.name == 'nt': cc = 'C:/Program Files/LLVM/bin/clang.exe'
    if not cc: parser.error('clang is required')
    modules = ('arena', 'gguf', 'unicode', 'tokenizer')
    for module in modules:
        subprocess.run([sys.executable, 'erlan/erlancompiler.py', f'services/model/{module}.e',
                        '-o', str(out / f'{module}.c'), '--header', str(out / f'{module}.h')], cwd=root, check=True)
    exe = out / ('probe.exe' if os.name == 'nt' else 'probe')
    subprocess.run([cc, '-std=c11', '-O2', '-Wno-unused-const-variable', '-Wno-unused-parameter',
                    '-Wno-unused-variable', '-Wno-parentheses-equality', '-Itests/model/stubs',
                    f'-I{out}', '-Ierlan/runtime', 'tests/model/tokenizer_probe.c',
                    *(str(out / f'{m}.c') for m in modules), '-o', str(exe)], cwd=root, check=True)
    fixture = json.loads((root / 'tests/model/qwen35_tokenizer.json').read_text(encoding='utf-8'))
    cases = fixture['cases']
    process = subprocess.run([str(exe), str(args.model.resolve())],
        input=''.join(c['text'].encode('utf-8').hex() + '\n' for c in cases),
        capture_output=True, text=True, encoding='utf-8', cwd=root)
    actual = [json.loads(line) for line in process.stdout.splitlines() if line.startswith('[')]
    if process.returncode or len(actual) != len(cases):
        raise RuntimeError(process.stderr or f'Incomplete probe: {len(actual)} cases')
    rows = [{**case, 'native': ids, 'passed': ids == case['tokens']} for case, ids in zip(cases, actual)]
    (out / 'result.json').write_text(json.dumps({'reference': fixture['reference'], 'cases': rows},
                                               indent=2, ensure_ascii=False), encoding='utf-8')
    for row in rows:
        if not row['passed']: print('FAIL', repr(row['text']), 'expected', row['tokens'], 'got', row['native'])
    passed = sum(row['passed'] for row in rows)
    print(f'QWEN_TOKENIZER {passed}/{len(rows)} exact IDs and byte roundtrips')
    return 0 if passed == len(rows) else 1

if __name__ == '__main__': sys.exit(main())
