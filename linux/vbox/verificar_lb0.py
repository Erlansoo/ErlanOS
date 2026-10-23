#!/usr/bin/env python3
"""Verifica el hito LB0: la VM "ErlanOS-Base" instaló Debian mínima sola y arranca.

Espera a que responda SSH con llave en 127.0.0.1:2222, recoge evidencia dentro de la VM
y escribe build/linux/lb0/<fecha>/result.json. Cada comprobación guarda la salida real.

    python linux/vbox/verificar_lb0.py [--timeout 3600] [--port 2222]
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "linux"
KEY = BUILD / "ssh" / "erlanos_vm"
KNOWN = BUILD / "ssh" / "known_hosts"
SERIAL = BUILD / "serial-lb0.log"
DESKTOP = ("xserver-xorg", "xwayland", "gnome-shell", "plasma-desktop", "xfce4", "lxde", "mate-desktop",
           "lightdm", "gdm3", "sddm")


def ssh_base(port: int) -> list[str]:
    exe = shutil.which("ssh")
    if not exe:
        sys.exit("no se encontró el cliente ssh")
    return [exe, "-i", str(KEY), "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN}",
            "-o", "LogLevel=ERROR", "erlan@127.0.0.1"]


def run(port: int, command: str, timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(ssh_base(port) + [command], capture_output=True, text=True, timeout=timeout,
                            encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def wait_ssh(port: int, timeout: int) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                sock.settimeout(5)
                if sock.recv(16).startswith(b"SSH-"):
                    code, _ = run(port, "true", timeout=30)
                    if code == 0:
                        return time.monotonic() - start
        except OSError:
            pass
        time.sleep(15)
    raise TimeoutError(f"SSH con llave no respondió en {timeout} s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()

    out_dir = BUILD / "lb0" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"suite": "lb0", "directory": str(out_dir), "checks": [], "evidence": {}}
    print(f"[lb0] esperando SSH con llave en 127.0.0.1:{args.port} (hasta {args.timeout} s)", flush=True)
    try:
        result["ssh_wait_s"] = round(wait_ssh(args.port, args.timeout), 1)
    except TimeoutError as exc:
        result["passed"] = False
        result["error"] = str(exc)
        (out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[lb0] FAIL: {exc}")
        return 1

    def check(name: str, command: str, predicate, timeout: int = 60):
        code, output = run(args.port, command, timeout)
        ok = code == 0 and bool(predicate(output))
        result["checks"].append({"name": name, "command": command, "exit": code, "output": output, "passed": ok})
        first = output.splitlines()[0] if output else ""
        print(f"  [{'ok' if ok else 'FALLO'}] {name}: {first[:110]}", flush=True)
        return output

    check("marca del hito", "cat /etc/erlanos-lb0", lambda o: "LB0" in o)
    check("Debian 13", ". /etc/os-release && echo $ID $VERSION_ID $VERSION_CODENAME", lambda o: o.startswith("debian 13"))
    check("kernel Linux", "uname -srm", lambda o: o.startswith("Linux "))
    check("systemd sin fallos", "systemctl is-system-running", lambda o: o.strip() in ("running", "degraded"))
    check("servicios fallidos", "systemctl --failed --no-legend --plain | wc -l", lambda o: o.strip() == "0")
    check("sin escritorio", "dpkg-query -W -f='${Package}\\n' | grep -E '^(" + "|".join(DESKTOP) + ")$' || echo ninguno",
          lambda o: o.strip() == "ninguno")
    check("SSH sin contraseñas", "sudo -n sshd -T | grep -i '^passwordauthentication'",
          lambda o: o.strip().lower() == "passwordauthentication no")
    check("root sin login", "sudo -n passwd -S root", lambda o: o.split()[1] in ("L", "NP"))
    check("zona horaria", "timedatectl show -p Timezone --value", lambda o: o.strip() == "America/La_Paz")
    check("red real (HTTPS)", "curl -fsS -o /dev/null -w '%{http_code}' https://deb.debian.org/debian/",
          lambda o: o.strip() == "200", timeout=90)
    check("herramientas para Erlan", "clang --version | head -1 && python3 --version && make --version | head -1",
          lambda o: "clang" in o and "Python 3" in o)

    evidence = {
        "procesos_total": "ps -e --no-headers | wc -l",
        "procesos_usuario": "ps -eo pid,ppid,user,comm --no-headers | awk '$1 != 2 && $2 != 2'",
        "servicios_activos": "systemctl list-units --type=service --state=running --no-legend --plain",
        "paquetes": "dpkg-query -W -f='${Package}\\n' | wc -l",
        "memoria": "free -m",
        "disco": "df -h /",
        "arranque": "systemd-analyze || true",
        "kernel_cmdline": "cat /proc/cmdline",
    }
    for key, command in evidence.items():
        _, output = run(args.port, command)
        result["evidence"][key] = output
    user_procs = [l for l in result["evidence"]["procesos_usuario"].splitlines() if l.strip()]
    result["evidence"]["procesos_usuario_total"] = len(user_procs)
    print(f"  procesos: {result['evidence']['procesos_total']} en total, {len(user_procs)} de espacio de usuario; "
          f"paquetes: {result['evidence']['paquetes']}", flush=True)
    print(f"  arranque: {result['evidence']['arranque'].splitlines()[0] if result['evidence']['arranque'] else '?'}")

    if SERIAL.is_file():
        shutil.copy2(SERIAL, out_dir / "serial.log")
        serial = SERIAL.read_text(encoding="utf-8", errors="replace")
        ok = "Linux version" in serial
        result["checks"].append({"name": "arranque visto por la consola serie", "command": str(SERIAL),
                                 "exit": 0, "output": f"{len(serial)} bytes", "passed": ok})
        print(f"  [{'ok' if ok else 'FALLO'}] consola serie: {len(serial)} bytes", flush=True)

    result["passed"] = all(c["passed"] for c in result["checks"])
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[lb0] {'PASS' if result['passed'] else 'FAIL'}: {out_dir / 'result.json'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
