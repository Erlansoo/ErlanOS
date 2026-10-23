#!/usr/bin/env python3
"""Genera net/tls/anchors.e: las raíces de confianza de curva elíptica (P-256 y P-384).

Fuentes: el almacén de raíces de Windows (ssl.enum_certificates) y, para raíces EC que
Windows valida por certificados cruzados, las cadenas que el propio sistema verificó.
Sólo se incluyen raíces vigentes con clave EC P-256/P-384 y uso para autenticar
servidores. Cada ancla guarda el Name del sujeto en DER (se compara byte a byte con
el emisor de un certificado) y la clave pública X||Y. El archivo generado lista el
nombre, la huella SHA-256 y la fecha de expiración de cada raíz para auditarlas.

    python scripts/gen_tls_anchors.py [--check]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ssl
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import ExtendedKeyUsageOID

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "net" / "tls" / "anchors.e"
# Raíces que usan los servicios que nos importan (Anthropic, OpenAI, Let's Encrypt,
# GitHub, Cloudflare). Si falta alguna en el almacén, el generador lo dice.
REQUIRED = ["GTS Root R4", "ISRG Root X2", "SSL.com TLS ECC Root CA 2022",
            "USERTrust ECC Certification Authority"]


def load_roots():
    if not hasattr(ssl, "enum_certificates"):
        sys.exit("este generador lee el almacén de Windows (ssl.enum_certificates)")
    seen, roots = set(), []
    now = dt.datetime.now(dt.timezone.utc)
    for store in ("ROOT", "AuthRoot"):
        try:
            entries = ssl.enum_certificates(store)
        except OSError:
            continue
        for der, encoding, trust in entries:
            if encoding != "x509_asn":
                continue
            if trust is not True and ExtendedKeyUsageOID.SERVER_AUTH.dotted_string not in trust:
                continue
            fp = hashlib.sha256(der).hexdigest()
            if fp in seen:
                continue
            seen.add(fp)
            try:
                cert = x509.load_der_x509_certificate(der)
            except ValueError:
                continue
            key = cert.public_key()
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name not in ("secp256r1", "secp384r1"):
                continue
            if cert.not_valid_after_utc < now:
                continue
            point = key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:]
            name = cert.subject.rfc4514_string()
            cn = [a.value for a in cert.subject if a.oid == x509.NameOID.COMMON_NAME]
            roots.append({"name": cn[0] if cn else name, "subject": cert.subject.public_bytes(),
                          "bits": 256 if key.curve.name == "secp256r1" else 384, "key": point,
                          "fingerprint": fp, "not_after": cert.not_valid_after_utc.date().isoformat()})
    roots.sort(key=lambda r: r["name"])
    return roots


def chunks(hexstr: str, width: int = 96):
    return [hexstr[i:i + width] for i in range(0, len(hexstr), width)]


def render(roots) -> str:
    total = sum(len(r["subject"]) + len(r["key"]) for r in roots)
    n = len(roots)
    lines = [
        "// GENERADO por scripts/gen_tls_anchors.py desde el almacén de raíces del anfitrión. No editar.",
        "// Anclas de confianza TLS con clave EC P-256/P-384: Name del sujeto (DER) y clave X||Y.",
        "//",
    ]
    for i, r in enumerate(roots):
        lines.append(f"// [{i:2d}] {r['name']} | P-{r['bits']} | vence {r['not_after']} | sha256 {r['fingerprint']}")
    lines += [
        "",
        f"const ANCHOR_COUNT: u64 = {n};",
        f"var anc_blob: [{max(total, 1)}]u8;",
        f"var anc_subj_off: [{max(n, 1)}]u64;",
        f"var anc_subj_len: [{max(n, 1)}]u64;",
        f"var anc_key_off: [{max(n, 1)}]u64;",
        f"var anc_bits: [{max(n, 1)}]u64;",
        "var anc_fill: u64 = 0;",
        "var anc_loaded: bool = false;",
        "",
        "fn anc_hexval(c: u8) -> u64 {",
        "    let v = c as u64;",
        "    if v >= 97 { return v - 87; }",
        "    if v >= 65 { return v - 55; }",
        "    return v - 48;",
        "}",
        "",
        "// Añade los bytes de una cadena hex al blob; devuelve cuántos añadió.",
        "fn anc_put(s: str) -> u64 {",
        "    var n: u64 = 0;",
        "    unsafe {",
        "        while s[2 * n] != 0 {",
        "            anc_blob[anc_fill + n] = ((anc_hexval(s[2 * n]) << 4) | anc_hexval(s[2 * n + 1])) as u8;",
        "            n += 1;",
        "        }",
        "    }",
        "    anc_fill += n;",
        "    return n;",
        "}",
        "",
        "fn anchors_load() {",
        "    if anc_loaded { return; }",
    ]
    for i, r in enumerate(roots):
        lines.append(f"    // {r['name']}")
        lines.append(f"    anc_subj_off[{i}] = anc_fill;")
        subj = chunks(r["subject"].hex())
        lines.append(f"    anc_subj_len[{i}] = anc_put(" + ("\n        ".join(f'"{c}"' for c in subj)) + ");")
        lines.append(f"    anc_key_off[{i}] = anc_fill;")
        key = chunks(r["key"].hex())
        lines.append("    anc_put(" + ("\n        ".join(f'"{c}"' for c in key)) + ");")
        lines.append(f"    anc_bits[{i}] = {r['bits']};")
    lines += [
        "    anc_loaded = true;",
        "}",
        "",
        "fn anchor_count() -> u64 { anchors_load(); return ANCHOR_COUNT; }",
        "fn anchor_bits(i: u64) -> u64 { anchors_load(); return anc_bits[i]; }",
        "fn anchor_subject(i: u64) -> []u8 { anchors_load(); return anc_blob[anc_subj_off[i]..anc_subj_off[i] + anc_subj_len[i]]; }",
        "fn anchor_key(i: u64) -> []u8 {",
        "    anchors_load();",
        "    let n = anc_bits[i] / 4;",
        "    return anc_blob[anc_key_off[i]..anc_key_off[i] + n];",
        "}",
        "",
    ]
    return "\n".join(lines)


def load_from_hosts(hosts):
    """Anclas EC tomadas de cadenas que el propio sistema anfitrión verificó.

    Algunas raíces EC modernas (GTS Root R4, SSL.com TLS ECC Root CA 2022) no están
    descargadas en el almacén de Windows, que las valida mediante certificados cruzados
    con raíces RSA antiguas. Si Windows verificó la cadena, el certificado CA de curva
    elíptica más alto de esa cadena queda avalado por el sistema: se usa su sujeto y su
    clave como ancla. La procedencia queda escrita en el archivo generado.
    """
    import socket
    ctx = ssl.create_default_context()
    found = []
    for host in hosts:
        try:
            with socket.create_connection((host, 443), timeout=10) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as tls:
                    chain = [x509.load_der_x509_certificate(der) for der in tls.get_verified_chain()]
        except (OSError, ssl.SSLError) as exc:
            print(f"aviso: {host}: {exc}")
            continue
        best = None
        for cert in chain[1:]:
            key = cert.public_key()
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name not in ("secp256r1", "secp384r1"):
                continue
            try:
                is_ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            except x509.ExtensionNotFound:
                is_ca = False
            if is_ca:
                best = cert
        if best is None:
            print(f"aviso: {host}: la cadena verificada no tiene CA de curva elíptica")
            continue
        key = best.public_key()
        cn = [a.value for a in best.subject if a.oid == x509.NameOID.COMMON_NAME]
        found.append({"name": (cn[0] if cn else best.subject.rfc4514_string()) + f" (cadena verificada por el SO: {host})",
                      "subject": best.subject.public_bytes(),
                      "bits": 256 if key.curve.name == "secp256r1" else 384,
                      "key": key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:],
                      "fingerprint": hashlib.sha256(best.public_bytes(Encoding.DER)).hexdigest(),
                      "not_after": best.not_valid_after_utc.date().isoformat()})
    return found


HOSTS = ["api.anthropic.com", "claude.ai", "chatgpt.com", "api.openai.com", "example.com",
         "github.com", "www.wikipedia.org"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="sólo listar, no escribir")
    parser.add_argument("--no-hosts", action="store_true", help="sólo el almacén local, sin cadenas verificadas")
    args = parser.parse_args()
    roots = load_roots()
    if not args.no_hosts:
        known = {(r["subject"], r["key"]) for r in roots}
        for extra in load_from_hosts(HOSTS):
            if (extra["subject"], extra["key"]) not in known:
                known.add((extra["subject"], extra["key"]))
                roots.append(extra)
        roots.sort(key=lambda r: r["name"])
    names = {r["name"].split(" (")[0] for r in roots}
    for r in roots:
        print(f"P-{r['bits']} {r['name']} (vence {r['not_after']})")
    missing = [name for name in REQUIRED if name not in names]
    print(f"{len(roots)} anclas EC; faltan requeridas: {missing or 'ninguna'}")
    if not args.check:
        OUT.write_text(render(roots), encoding="utf-8")
        print(f"escrito {OUT}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
