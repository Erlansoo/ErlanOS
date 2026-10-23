#!/usr/bin/env python3
"""Prueba automatizada de red N0/N1: ErlanOS + driver e1000 + pila net/ en QEMU con
red de usuario (SLIRP). Arranca una imagen privada (kernel-net.elf, sin modelo ni
servicios), espera los marcadores del serial (ARP, DHCP, DNS, TCP, HTTP) y verifica
de forma independiente:

- el pcap capturado por QEMU (tramas reales de la NIC),
- la IP resuelta contra el resolver del host,
- un servidor TCP propio en el anfitrión (subida exacta y SHA-256 de la bajada),
- el código de estado HTTP de example.com pedido también desde el host.

Build: ``make build/kernel-net.elf`` (o ``make test-net``).
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from build_boot_image import build_image
from build_hardware_image import hardware_files
from qemu_accel import accel_args
from qemu_m4_test import QemuSession, ROOT, TestFailure, common_arguments

GUEST_MAC = "52:54:00:12:34:56"
MARK_READY = "[net] probe: ready"
RE_ARP = re.compile(r"\[net\] arp ok gw=(\S+) mac=(\S+)")
RE_DHCP = re.compile(r"\[net\] dhcp ok ip=(\S+) mask=(\S+) gw=(\S+) dns=(\S+) lease=(\d+)s")
RE_DNS = re.compile(r"\[net\] dns ok example\.com=(\d+\.\d+\.\d+\.\d+)")
RE_BULK = re.compile(r"\[net\] tcp bulk ok subida=(\d+) bajada=(\d+) sha256=([0-9a-f]{64}) "
                     r"retrans=(\d+) descartados=(\d+) en (\d+)ms")
RE_HTTP = re.compile(r"\[net\] http ok bytes=(\d+) en (\d+)ms estado=(HTTP/1\.[01] (\d{3})[^\r\n]*)")
RE_FAIL = re.compile(r"\[net\] FALLO[^\n]*")

BULK_PORT = 18765
BULK_UP = 6000
BULK_DOWN = 300000


class BulkServer(threading.Thread):
    """Servidor TCP del anfitrión (el guest lo ve como 10.0.2.2:18765)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.create_server(("127.0.0.1", BULK_PORT))
        self.upload = bytes(((i * 7 + 13) & 0xFF) for i in range(BULK_UP))
        self.download = bytes(((i * 31 + (i >> 8)) & 0xFF) for i in range(BULK_DOWN))
        self.download_sha256 = hashlib.sha256(self.download).hexdigest()
        self.received = 0
        self.upload_ok = None
        self.sent = 0
        self.error = None

    def run(self):
        try:
            self.sock.settimeout(900)
            conn, _ = self.sock.accept()
            with conn:
                conn.settimeout(120)
                data = b""
                while len(data) < BULK_UP:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                self.received = len(data)
                self.upload_ok = data == self.upload
                conn.sendall(self.download)
                self.sent = len(self.download)
        except Exception as exc:  # noqa: BLE001
            self.error = repr(exc)
        finally:
            self.sock.close()


def parse_pcap(path: Path) -> list[bytes]:
    data = path.read_bytes()
    if len(data) < 24:
        raise TestFailure("pcap vacío o truncado")
    magic = struct.unpack("<I", data[:4])[0]
    if magic == 0xA1B2C3D4:
        endian = "<"
    elif magic == 0xD4C3B2A1:
        endian = ">"
    else:
        raise TestFailure(f"pcap con magic desconocido {magic:#x}")
    link = struct.unpack(endian + "I", data[20:24])[0]
    if link != 1:
        raise TestFailure(f"pcap no es Ethernet (linktype {link})")
    frames, pos = [], 24
    while pos + 16 <= len(data):
        _, _, incl, _ = struct.unpack(endian + "IIII", data[pos:pos + 16])
        pos += 16
        frames.append(data[pos:pos + incl])
        pos += incl
    return frames


def mac_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def summarize_pcap(frames: list[bytes]) -> dict:
    s = {"frames": len(frames), "arp_request_from_guest": 0, "arp_reply_to_guest": 0,
         "dhcp_discover": 0, "dhcp_offer": 0, "dhcp_request": 0, "dhcp_ack": 0,
         "dns_query": 0, "dns_response": 0, "udp_bad": 0,
         "tcp_syn_from_guest": 0, "tcp_synack_to_guest": 0, "tcp_segments": 0, "tcp_fin": 0}
    for f in frames:
        if len(f) < 14:
            continue
        dst, src, eth = f[:6], f[6:12], struct.unpack(">H", f[12:14])[0]
        if eth == 0x0806 and len(f) >= 42:
            oper = struct.unpack(">H", f[20:22])[0]
            if oper == 1 and mac_str(src) == GUEST_MAC:
                s["arp_request_from_guest"] += 1
            if oper == 2 and mac_str(dst) == GUEST_MAC:
                s["arp_reply_to_guest"] += 1
        elif eth == 0x0800 and len(f) >= 34 and f[23] == 6:
            ihl = (f[14] & 0xF) * 4
            t = 14 + ihl
            if len(f) < t + 20:
                continue
            flags = f[t + 13]
            s["tcp_segments"] += 1
            if flags & 0x02 and not flags & 0x10 and mac_str(src) == GUEST_MAC:
                s["tcp_syn_from_guest"] += 1
            if flags & 0x12 == 0x12 and mac_str(dst) == GUEST_MAC:
                s["tcp_synack_to_guest"] += 1
            if flags & 0x01:
                s["tcp_fin"] += 1
        elif eth == 0x0800 and len(f) >= 34 and f[23] == 17:
            ihl = (f[14] & 0xF) * 4
            u = 14 + ihl
            if len(f) < u + 8:
                s["udp_bad"] += 1
                continue
            sport, dport = struct.unpack(">HH", f[u:u + 4])
            payload = f[u + 8:]
            if sport == 68 and dport == 67 and len(payload) > 240:
                kind = dhcp_type(payload)
                if kind == 1:
                    s["dhcp_discover"] += 1
                elif kind == 3:
                    s["dhcp_request"] += 1
            elif sport == 67 and dport == 68 and len(payload) > 240:
                kind = dhcp_type(payload)
                if kind == 2:
                    s["dhcp_offer"] += 1
                elif kind == 5:
                    s["dhcp_ack"] += 1
            elif dport == 53:
                s["dns_query"] += 1
            elif sport == 53:
                s["dns_response"] += 1
    return s


def dhcp_type(payload: bytes) -> int:
    if payload[236:240] != b"\x63\x82\x53\x63":
        return 0
    o = 240
    while o + 1 < len(payload):
        code = payload[o]
        if code == 255:
            break
        if code == 0:
            o += 1
            continue
        ln = payload[o + 1]
        if code == 53 and ln >= 1:
            return payload[o + 2]
        o += 2 + ln
    return 0


class NetSession(QemuSession):
    def __init__(self, args):
        super().__init__(args, suite="net")
        self.pcap = self.directory / "net.pcap"

    def prepare(self):
        kernel = Path(self.args.kernel)
        if not kernel.is_file():
            raise TestFailure(f"Falta {kernel}: compila con make build/kernel-net.elf")
        files = hardware_files(kernel, "ErlanOS Red N0")
        import shutil
        shutil.copy2(self.ovmf, self.directory / "firmware.fd")
        self.result["boot_image"] = build_image(self.directory / "disk.img", files)
        self.result["inputs"] = self.result["boot_image"]["files"]

    def start(self):
        self.prepare()
        accelerator = accel_args(self.args.accel)
        if getattr(self.args, "cpu", None):
            accelerator[accelerator.index("-cpu") + 1] = self.args.cpu
        command = [self.qemu, "-M", "q35", "-m", self.args.memory, *accelerator,
                   "-drive", "if=pflash,format=raw,readonly=on,file=firmware.fd",
                   "-drive", "format=raw,file=disk.img", "-serial", "file:serial.log",
                   "-netdev", "user,id=n0,ipv6=off",
                   "-device", f"e1000,netdev=n0,mac={GUEST_MAC}",
                   "-object", "filter-dump,id=f0,netdev=n0,file=net.pcap",
                   "-qmp", "stdio", "-monitor", "none", "-display", "none",
                   "-no-reboot", "-no-shutdown"]
        self.result["qemu_command"] = command
        self.stderr_file = self.stderr_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(command, cwd=self.directory, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr_file,
                                     text=True, encoding="utf-8", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.result["qemu_pid"] = self.proc.pid
        (self.directory / "qemu.pid").write_text(str(self.proc.pid) + "\n", encoding="ascii")
        threading.Thread(target=self._reader, daemon=True).start()
        greeting = self._message(15)
        if "QMP" not in greeting:
            raise TestFailure(f"Invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")


def host_resolve(name: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(name, 80, socket.AF_INET)}
    except OSError:
        return set()


def host_http_status() -> int | None:
    try:
        conn = http.client.HTTPConnection("example.com", 80, timeout=15)
        conn.request("GET", "/", headers={"User-Agent": "ErlanOS/0.1", "Accept": "*/*", "Connection": "close"})
        status = conn.getresponse().status
        conn.close()
        return status
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_arguments(parser)
    parser.add_argument("--kernel", type=Path, default=ROOT / "build/kernel-net.elf")
    parser.set_defaults(memory="512M", timeout=600)
    args = parser.parse_args()
    server = BulkServer()
    server.start()
    vm = NetSession(args)
    failure = None
    try:
        vm.start()
        serial = vm.wait_for(lambda text: MARK_READY in text, "prueba de red completa", args.timeout)
        vm.capture()
        stages = {"arp": RE_ARP.search(serial), "dhcp": RE_DHCP.search(serial), "dns": RE_DNS.search(serial),
                  "tcp_bulk": RE_BULK.search(serial), "http": RE_HTTP.search(serial)}
        vm.result["stages"] = {k: bool(v) for k, v in stages.items()}
        vm.result["failures"] = RE_FAIL.findall(serial)
        for line in serial.splitlines():
            if line.startswith("[net]") or line.startswith("[e1000]"):
                print(line, flush=True)
        if not stages["arp"]:
            raise TestFailure("ARP al gateway no verificado en el serial")
        if not stages["dhcp"]:
            raise TestFailure("DHCP no verificado en el serial")
        ip, mask, gw, dns, lease = stages["dhcp"].groups()
        if ip != "10.0.2.15" or gw != "10.0.2.2" or dns != "10.0.2.3":
            raise TestFailure(f"Lease inesperado de SLIRP: ip={ip} gw={gw} dns={dns}")
        if not stages["dns"]:
            raise TestFailure("DNS no verificado en el serial")
        guest_ip = stages["dns"].group(1)
        vm.result["resolved_ip"] = guest_ip

        # Oráculo 1: el resolver real del host (SLIRP reenvía a él).
        host_ips = host_resolve("example.com")
        vm.result["host_resolved"] = sorted(host_ips)
        if not host_ips:
            raise TestFailure("El host no pudo resolver example.com: sin internet en el host, no se puede validar")
        if guest_ip not in host_ips:
            raise TestFailure(f"La IP del guest {guest_ip} no coincide con el host {sorted(host_ips)}")
        print(f"[test] example.com -> {guest_ip} (host: {sorted(host_ips)})", flush=True)

        # Oráculo 3: servidor TCP del anfitrión (subida exacta, SHA-256 de la bajada).
        server.join(timeout=10)
        vm.result["tcp_server"] = {"received": server.received, "upload_ok": server.upload_ok,
                                   "sent": server.sent, "sha256": server.download_sha256, "error": server.error}
        if not stages["tcp_bulk"]:
            raise TestFailure(f"TCP masivo no verificado en el serial (servidor: {vm.result['tcp_server']})")
        up, down, digest, retrans, discarded, ms = stages["tcp_bulk"].groups()
        vm.result["tcp_bulk"] = {"up": int(up), "down": int(down), "sha256": digest, "retransmits": int(retrans),
                                 "discarded": int(discarded), "ms": int(ms)}
        if not server.upload_ok:
            raise TestFailure(f"El servidor no recibió la subida exacta: {vm.result['tcp_server']}")
        if int(down) != BULK_DOWN or digest != server.download_sha256:
            raise TestFailure(f"Bajada corrupta: guest {down} bytes sha256={digest}, esperado {server.download_sha256}")
        kbps = int(down) * 8 / max(1, int(ms))
        print(f"[test] tcp: subida verificada por el host, bajada {down} bytes con SHA-256 idéntico "
              f"({kbps:.0f} kbit/s, retrans={retrans}, descartados={discarded})", flush=True)

        # Oráculo 4: HTTP real, mismo código de estado pedido desde el host.
        if not stages["http"]:
            raise TestFailure("HTTP a example.com no verificado en el serial")
        http_bytes, http_ms, status_line, code = stages["http"].groups()
        host_code = host_http_status()
        vm.result["http"] = {"bytes": int(http_bytes), "ms": int(http_ms), "status_line": status_line,
                             "guest_code": int(code), "host_code": host_code}
        if host_code is None:
            raise TestFailure("El host no pudo hacer HTTP a example.com: no se puede validar")
        if int(code) != host_code:
            raise TestFailure(f"Código HTTP distinto: guest {code}, host {host_code}")
        print(f"[test] http: '{status_line}' ({http_bytes} bytes); host obtuvo {host_code}", flush=True)

        # Oráculo 2: captura pcap de QEMU (tramas reales que salieron y entraron por la NIC).
        time.sleep(1)
        if not vm.pcap.is_file():
            raise TestFailure("QEMU no escribió net.pcap")
        summary = summarize_pcap(parse_pcap(vm.pcap))
        vm.result["pcap"] = summary
        print("[test] pcap:", json.dumps(summary), flush=True)
        for key in ("arp_request_from_guest", "arp_reply_to_guest", "dhcp_discover", "dhcp_offer",
                    "dhcp_request", "dhcp_ack", "dns_query", "dns_response"):
            if summary[key] < 1:
                raise TestFailure(f"pcap sin tramas de tipo {key}")
        if summary["tcp_syn_from_guest"] < 2 or summary["tcp_synack_to_guest"] < 2:
            raise TestFailure("pcap sin los dos saludos TCP (SYN del guest y SYN-ACK de vuelta)")
        vm.result["passed"] = True
        print("PASS N1: DHCP, DNS, TCP propio (300 KB con SHA-256 nativo) y HTTP real a example.com "
              "(verificado por serial, pcap, servidor del host y resolver/HTTP del host)", flush=True)
    except Exception as exc:  # noqa: BLE001
        failure = exc
    return vm.finish(failure)


if __name__ == "__main__":
    raise SystemExit(main())
