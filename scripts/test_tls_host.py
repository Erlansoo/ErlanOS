#!/usr/bin/env python3
"""Pruebas en anfitrión de la cripto nativa de ErlanOS (net/tls/*.e).

Compila los módulos Erlan a C, los enlaza con tests/tls/crypto_host.c y compara cada
resultado con oráculos independientes: hashlib/hmac (OpenSSL), la librería
cryptography (HKDF, AES, AES-GCM, X25519, ECDSA) y vectores fijos de FIPS y RFC.

    python scripts/test_tls_host.py [--seed N] [--count N]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as pyhmac
import random
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "tls-host"
HASHES = {0: (hashlib.sha256, hashes.SHA256), 1: (hashlib.sha384, hashes.SHA384)}


def run(cmd):
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(" ".join(map(str, cmd)))
        print(result.stdout)
        print(result.stderr)
        sys.exit("fallo de compilacion")


def build() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = []
    for src in sorted((ROOT / "net" / "tls").glob("*.e")):
        c_file, h_file = OUT / f"{src.stem}.c", OUT / f"{src.stem}.h"
        run([sys.executable, "erlan/erlancompiler.py", str(src), "-o", str(c_file), "--header", str(h_file)])
        sources.append(str(c_file))
    exe = OUT / "crypto_host.exe"
    run(["clang", "-O2", "-g", "-Wall", "-Wno-unused-variable", "-Wno-unused-but-set-variable",
         "-Wno-unused-function", "-Ierlan/runtime", f"-I{OUT}", "tests/tls/crypto_host.c", *sources,
         "erlan/tests/host_panic.c", "-o", str(exe)])
    return exe


def hx(data: bytes) -> str:
    return data.hex() if data else "-"


def vectors(rng: random.Random, count: int):
    out = []

    def rb(n):
        return bytes(rng.getrandbits(8) for _ in range(n))

    def add(label, line, expected):
        out.append((label, line, expected))

    # Vectores fijos (protegen contra un oráculo mal usado)
    add("sha256 FIPS abc", "sha256 616263", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    add("sha384 FIPS abc", "sha384 616263",
        "cb00753f45a35e8bb5a03d699ac65007272c32ab0eded1631a8b605a43ff5bed8086072ba1e7cc2358baeca134c825a7")
    add("sha512 FIPS abc", "sha512 616263",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f")
    add("aes128 FIPS-197 C.1", "aes 000102030405060708090a0b0c0d0e0f 00112233445566778899aabbccddeeff",
        "69c4e0d86a7b0430d8cdb78070b4c55a")
    add("aes256 FIPS-197 C.3",
        "aes 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f 00112233445566778899aabbccddeeff",
        "8ea2b7ca516745bfeafc49904b496089")
    add("x25519 RFC 7748 §5.2",
        "x25519 a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4 "
        "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
        "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")
    add("x25519 RFC 7748 §6.1 pub",
        "x25519pub 77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a",
        "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")

    lengths = [0, 1, 3, 55, 56, 57, 63, 64, 65, 111, 112, 113, 119, 120, 127, 128, 129, 200, 1000, 4096]
    for n in lengths + [rng.randrange(0, 3000) for _ in range(count)]:
        m = rb(n)
        add(f"sha256 len={n}", f"sha256 {hx(m)}", hashlib.sha256(m).hexdigest())
        add(f"sha384 len={n}", f"sha384 {hx(m)}", hashlib.sha384(m).hexdigest())
        add(f"sha512 len={n}", f"sha512 {hx(m)}", hashlib.sha512(m).hexdigest())

    for kind, (hfn, halg) in HASHES.items():
        hl = hfn().digest_size
        for klen in [0, 1, 20, 32, 48, 64, 65, 128, 129, 200]:
            key, msg = rb(klen), rb(rng.randrange(0, 300))
            add(f"hmac{kind} k={klen}", f"hmac {kind} {hx(key)} {hx(msg)}", pyhmac.new(key, msg, hfn).hexdigest())
        for slen in [0, 32, 48]:
            salt, ikm = rb(slen), rb(hl)
            add(f"hkdfext{kind} salt={slen}", f"hkdfext {kind} {hx(salt)} {hx(ikm)}",
                pyhmac.new(salt if salt else b"\0" * hl, ikm, hfn).hexdigest())
        for length in [1, 12, 16, 32, 48, 64, 100, 255]:
            prk, info = rb(hl), rb(rng.randrange(0, 40))
            add(f"hkdfexp{kind} L={length}", f"hkdfexp {kind} {hx(prk)} {hx(info)} {length}",
                HKDFExpand(halg(), length, info).derive(prk).hex())
        for label, length in [("derived", hl), ("c hs traffic", hl), ("key", 16), ("key", 32), ("iv", 12),
                              ("finished", hl), ("s ap traffic", hl)]:
            secret, ctx = rb(hl), rb(rng.choice([0, 32, 48]))
            info = (length.to_bytes(2, "big") + bytes([6 + len(label)]) + b"tls13 " + label.encode()
                    + bytes([len(ctx)]) + ctx)
            add(f"label{kind} {label}", f"label {kind} {hx(secret)} {label.encode().hex()} {hx(ctx)} {length}",
                HKDFExpand(halg(), length, info).derive(secret).hex())

    for klen in (16, 32):
        for _ in range(count):
            key, block = rb(klen), rb(16)
            enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
            add(f"aes{klen * 8}", f"aes {hx(key)} {hx(block)}", (enc.update(block) + enc.finalize()).hex())
        for n in [0, 1, 15, 16, 17, 31, 64, 100, 1000, 16401] + [rng.randrange(0, 2000) for _ in range(count)]:
            key, iv, aad, pt = rb(klen), rb(12), rb(rng.choice([0, 5, 13, 16, 17])), rb(n)
            ct = AESGCM(key).encrypt(iv, pt, aad)
            add(f"gcmseal{klen * 8} n={n}", f"gcmseal {hx(key)} {hx(iv)} {hx(aad)} {hx(pt)}", ct.hex())
            add(f"gcmopen{klen * 8} n={n}", f"gcmopen {hx(key)} {hx(iv)} {hx(aad)} {ct.hex()}", hx(pt))
            bad = bytearray(ct)
            bad[rng.randrange(len(bad))] ^= 1 << rng.randrange(8)
            add(f"gcmopen{klen * 8} alterado n={n}", f"gcmopen {hx(key)} {hx(iv)} {hx(aad)} {bad.hex()}", "FAIL")

    for _ in range(count):
        a, b = X25519PrivateKey.generate(), X25519PrivateKey.generate()
        araw = a.private_bytes_raw()
        add("x25519pub", f"x25519pub {araw.hex()}", a.public_key().public_bytes_raw().hex())
        add("x25519", f"x25519 {araw.hex()} {b.public_key().public_bytes_raw().hex()}", a.exchange(b.public_key()).hex())

    for curve, bits in [(ec.SECP256R1(), 256), (ec.SECP384R1(), 384)]:
        nb = bits // 8
        for hname, halg in [("sha256", hashes.SHA256), ("sha384", hashes.SHA384)]:
            for _ in range(count):
                key = ec.generate_private_key(curve)
                msg = rb(rng.randrange(1, 200))
                r, s = decode_dss_signature(key.sign(msg, ec.ECDSA(halg())))
                nums = key.public_key().public_numbers()
                pub = nums.x.to_bytes(nb, "big") + nums.y.to_bytes(nb, "big")
                digest = hashlib.new(hname, msg).digest()
                rs = r.to_bytes(nb, "big") + s.to_bytes(nb, "big")
                add(f"ecdsa P-{bits}/{hname}", f"ecdsa {bits} {pub.hex()} {digest.hex()} {rs.hex()}", "OK")
                bad = bytearray(digest)
                bad[rng.randrange(min(len(bad), nb))] ^= 1   # solo cuentan los primeros nb bytes del hash
                add(f"ecdsa P-{bits}/{hname} hash alterado", f"ecdsa {bits} {pub.hex()} {bytes(bad).hex()} {rs.hex()}", "FAIL")
                bad_sig = bytearray(rs)
                bad_sig[rng.randrange(len(bad_sig))] ^= 4
                add(f"ecdsa P-{bits}/{hname} firma alterada", f"ecdsa {bits} {pub.hex()} {digest.hex()} {bad_sig.hex()}", "FAIL")
                off_curve = nums.x.to_bytes(nb, "big") + ((nums.y + 1) % (1 << bits)).to_bytes(nb, "big")
                add(f"ecdsa P-{bits} punto fuera de la curva", f"ecdsa {bits} {off_curve.hex()} {digest.hex()} {rs.hex()}", "FAIL")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    exe = build()
    rng = random.Random(args.seed)
    cases = vectors(rng, args.count)
    stdin = "\n".join(line for _, line, _ in cases) + "\n"
    result = subprocess.run([str(exe)], input=stdin, capture_output=True, text=True)
    outputs = result.stdout.splitlines()
    if result.returncode != 0:
        print(result.stdout[-3000:])
        print(f"el arnés terminó con código {result.returncode} tras {len(outputs)} respuestas")
        if len(outputs) < len(cases):
            print("caso en curso:", cases[len(outputs)][0])
        return 1
    failures, per_op = [], {}
    for i, (label, line, expected) in enumerate(cases):
        got = outputs[i] if i < len(outputs) else "<sin salida>"
        op = line.split()[0]
        total, ok = per_op.get(op, (0, 0))
        per_op[op] = (total + 1, ok + (got == expected))
        if got != expected:
            failures.append((label, expected[:96], got[:96], line))
    for op, (total, ok) in per_op.items():
        print(f"  {op:10s} {ok}/{total}")
    report = OUT / "failures.txt"
    report.write_text("".join(f"{f[0]}\n{f[3]}\nesperado {f[1]}\nobtenido {f[2]}\n\n" for f in failures), encoding="utf-8")
    if failures:
        print(f"detalle completo de los fallos: {report}")
        for label, expected, got, _ in failures[:12]:
            print(f"FALLO {label}\n  esperado {expected}\n  obtenido {got}")
        print(f"TLS_HOST FAIL: {len(failures)} de {len(cases)} vectores")
        return 1
    print(f"TLS_HOST PASS: {len(cases)} vectores (seed={args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
