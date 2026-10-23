#!/usr/bin/env python3
"""Select a working hardware accelerator, with an explicit TCG fallback."""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time


def accel_args(name):
    # OVMF tries to enable nested VMX when exposed by "max". WHPX rejects that
    # IA32_FEATURE_CONTROL write; ErlanOS does not need nested virtualization.
    cpu = "host" if name == "kvm" else "max,vmx=off" if name == "whpx" else "max"
    return ["-accel", name, "-cpu", cpu]


def probe(qemu, name, timeout=10):
    """Initialize a paused, diskless VM and perform a sequential QMP handshake."""
    command = [qemu, "-M", "q35", "-m", "64M", *accel_args(name),
               "-S", "-nodefaults", "-display", "none", "-monitor", "none", "-qmp", "stdio"]
    child = None
    errors = []
    messages = queue.Queue()
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read_messages():
            for line in child.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            messages.put(None)

        def read_errors():
            for line in child.stderr:
                errors.append(line)

        threading.Thread(target=read_messages, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()

        def receive():
            value = messages.get(timeout=max(.01, deadline - time.monotonic()))
            if value is None:
                raise RuntimeError("QEMU closed QMP")
            return value

        if "QMP" not in receive():
            raise RuntimeError("Missing QMP greeting")
        for number, command_name in enumerate(("qmp_capabilities", "query-status", "quit"), 1):
            child.stdin.write(json.dumps({"execute": command_name, "id": number}) + "\n")
            child.stdin.flush()
            while True:
                response = receive()
                if response.get("id") == number:
                    if "error" in response:
                        raise RuntimeError(str(response["error"]))
                    break
        child.wait(timeout=max(.01, deadline - time.monotonic()))
        if child.returncode:
            raise RuntimeError(f"QEMU exited {child.returncode}")
        return True, ""
    except (OSError, RuntimeError, queue.Empty, subprocess.TimeoutExpired) as error:
        return False, ("".join(errors).strip() or str(error) or "QMP timeout")
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)


def select(qemu, requested="auto"):
    if requested == "tcg":
        return "tcg", "Explicit software emulation"
    candidates = (["whpx"] if os.name == "nt" else
                  ["kvm"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else [])
    if requested != "auto":
        candidates = [requested]
    failures = []
    for candidate in candidates:
        ok, reason = probe(qemu, candidate)
        if ok:
            return candidate, "QMP initialization verified"
        failures.append(f"{candidate}: {reason}")
    if requested != "auto":
        raise RuntimeError("; ".join(failures))
    return "tcg", "; ".join(failures) or "No available hardware accelerator"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--accel", choices=("auto", "whpx", "kvm", "tcg"), default="auto")
    parser.add_argument("--flags", action="store_true", help="Print only arguments for make run")
    args = parser.parse_args()
    qemu = shutil.which(args.qemu)
    if not qemu:
        parser.error(f"QEMU not found: {args.qemu}")
    try:
        selected, reason = select(qemu, args.accel)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    if args.flags:
        print(" ".join(accel_args(selected)))
        if selected == "tcg" and args.accel == "auto":
            print("[QEMU] TCG: " + reason, file=sys.stderr)
    else:
        print(json.dumps({"accelerator": selected, "arguments": accel_args(selected),
                          "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
