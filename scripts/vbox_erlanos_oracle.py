#!/usr/bin/env python3
"""Oráculos del anfitrión para la prueba de ErlanOS en VirtualBox (scripts/vbox_erlanos.ps1).

Dos modos, los dos sin tocar la VM:

    serve   --serial LOG --timeout S --out JSON
        Servidor TCP en 127.0.0.1:18765 (el guest lo ve como 10.0.2.2 por el NAT de
        VirtualBox), con los mismos datos que la prueba de QEMU: subida de 6000 bytes
        con patrón fijo y bajada de 300000 bytes cuyo SHA-256 el kernel calcula solo.
        Termina cuando el serial muestra `[net] probe: ready` o vence el plazo, y deja
        en JSON lo que recibió y envió.

    verify  --serial LOG --pcap PCAP --server JSON --out JSON
        Lee el serial del guest (marcadores `[net] ...`), la traza pcap que escribió
        VirtualBox (`--nic-trace-file`) y compara con el resolver y el HTTP del propio
        anfitrión. Es el mismo criterio de `scripts/qemu_net_test.py`, sin QEMU.

No instala nada, no abre dispositivos físicos y no modifica la VM.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import re

import qemu_net_test
from qemu_net_test import (BULK_DOWN, MARK_READY, RE_ARP, RE_BULK, RE_DHCP, RE_DNS, RE_FAIL, RE_HTTP,
                           BulkServer, host_http_status, host_resolve, parse_pcap, summarize_pcap)

RE_ARP_DNS = re.compile(r"\[net\] arp ok dns=(\S+)(?: via=(\S+))? mac=(\S+)")
RE_MAC = re.compile(r"\[net\] mac ([0-9a-f:]{17})")
RE_LINK = re.compile(r"\[net\] (enlace arriba en|FALLO enlace: sigue abajo tras) (\d+)ms")


def read_serial(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def serve(args) -> int:
    server = BulkServer()
    server.start()
    deadline = time.monotonic() + args.timeout
    outcome = {"port": 18765, "ready_marker": False, "timed_out": False}
    while time.monotonic() < deadline:
        if MARK_READY in read_serial(args.serial):
            outcome["ready_marker"] = True
            break
        if not server.is_alive() and server.error:
            break
        time.sleep(0.5)
    else:
        outcome["timed_out"] = True
    server.join(timeout=5)
    outcome.update({"received": server.received, "upload_ok": server.upload_ok, "sent": server.sent,
                    "download_sha256": server.download_sha256, "error": server.error,
                    "finished": server.received > 0 or server.error is not None})
    args.out.write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome), flush=True)
    return 0


def verify(args) -> int:
    serial = read_serial(args.serial)
    result: dict = {"serial": str(args.serial), "stages": {}, "failures": RE_FAIL.findall(serial),
                    "ready_marker": MARK_READY in serial, "passed_basic": False, "passed_all": False,
                    "reasons": []}
    mac = RE_MAC.search(serial)
    result["guest_mac"] = mac.group(1) if mac else None
    link = RE_LINK.search(serial)
    result["link"] = {"up": link.group(1).startswith("enlace"), "ms": int(link.group(2))} if link else None
    stages = {"arp": RE_ARP.search(serial), "dhcp": RE_DHCP.search(serial), "arp_dns": RE_ARP_DNS.search(serial),
              "dns": RE_DNS.search(serial), "tcp_bulk": RE_BULK.search(serial), "http": RE_HTTP.search(serial)}
    result["stages"] = {k: bool(v) for k, v in stages.items()}
    reasons = result["reasons"]
    if stages["arp"]:
        result["gateway"] = {"ip": stages["arp"].group(1), "mac": stages["arp"].group(2)}
    else:
        reasons.append("ARP al gateway no verificado en el serial")
    if stages["dhcp"]:
        ip, mask, gw, dns, lease = stages["dhcp"].groups()
        result["lease"] = {"ip": ip, "mask": mask, "gw": gw, "dns": dns, "lease_s": int(lease)}
    else:
        reasons.append("DHCP no verificado en el serial")
    if stages["dns"]:
        guest_ip = stages["dns"].group(1)
        result["resolved_ip"] = guest_ip
        host_ips = host_resolve("example.com")
        result["host_resolved"] = sorted(host_ips)
        if not host_ips:
            reasons.append("El anfitrión no pudo resolver example.com: sin internet, no se puede validar")
        elif guest_ip not in host_ips:
            reasons.append(f"La IP del guest {guest_ip} no coincide con el resolver del anfitrión {sorted(host_ips)}")
    else:
        reasons.append("DNS no verificado en el serial")

    # pcap de VirtualBox: tramas reales que pasaron por la NIC emulada. La MAC la asigna
    # VirtualBox (08:00:27:xx:xx:xx), no es la fija de QEMU: se toma del serial.
    if args.pcap and args.pcap.is_file() and args.pcap.stat().st_size > 24:
        try:
            if mac:
                qemu_net_test.GUEST_MAC = mac.group(1)
            summary = summarize_pcap(parse_pcap(args.pcap))
            result["pcap"] = summary
            for key in ("arp_request_from_guest", "arp_reply_to_guest", "dhcp_discover", "dhcp_offer",
                        "dhcp_request", "dhcp_ack", "dns_query", "dns_response"):
                if summary.get(key, 0) < 1:
                    reasons.append(f"pcap sin tramas de tipo {key}")
        except Exception as exc:  # noqa: BLE001
            result["pcap_error"] = repr(exc)
            reasons.append(f"pcap ilegible: {exc!r}")
    else:
        result["pcap"] = None
        reasons.append("VirtualBox no dejó traza pcap de la NIC")
    result["passed_basic"] = not reasons

    # Etapas de transporte: se informan aparte; no condicionan el resultado básico.
    extra: list[str] = []
    server = {}
    if args.server and args.server.is_file():
        server = json.loads(args.server.read_text(encoding="utf-8"))
    result["tcp_server"] = server
    if stages["tcp_bulk"]:
        up, down, digest, retrans, discarded, ms = stages["tcp_bulk"].groups()
        result["tcp_bulk"] = {"up": int(up), "down": int(down), "sha256": digest, "retransmits": int(retrans),
                              "discarded": int(discarded), "ms": int(ms)}
        if not server.get("upload_ok"):
            extra.append("el servidor del anfitrión no recibió la subida exacta")
        if int(down) != BULK_DOWN or digest != server.get("download_sha256"):
            extra.append("la bajada TCP no coincide con el SHA-256 del anfitrión")
    else:
        extra.append("TCP masivo no verificado en el serial")
    if stages["http"]:
        http_bytes, http_ms, status_line, code = stages["http"].groups()
        host_code = host_http_status()
        result["http"] = {"bytes": int(http_bytes), "ms": int(http_ms), "status_line": status_line,
                          "guest_code": int(code), "host_code": host_code}
        if host_code is None:
            extra.append("el anfitrión no pudo hacer HTTP a example.com")
        elif int(code) != host_code:
            extra.append(f"código HTTP distinto: guest {code}, anfitrión {host_code}")
    else:
        extra.append("HTTP a example.com no verificado en el serial")
    result["transport_reasons"] = extra
    result["passed_all"] = result["passed_basic"] and not extra
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed_basic": result["passed_basic"], "passed_all": result["passed_all"],
                      "reasons": reasons, "transport_reasons": extra}, ensure_ascii=False), flush=True)
    return 0 if result["passed_basic"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--serial", type=Path, required=True)
    s.add_argument("--timeout", type=float, default=600)
    s.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--serial", type=Path, required=True)
    v.add_argument("--pcap", type=Path)
    v.add_argument("--server", type=Path)
    v.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return serve(args) if args.mode == "serve" else verify(args)


if __name__ == "__main__":
    sys.exit(main())
