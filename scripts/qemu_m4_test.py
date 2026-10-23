#!/usr/bin/env python3
"""Exercise M4's real IPC, capability checks, receipts and console in QEMU.

Build first (``source scripts/env.sh && make esp``), then run:
    python scripts/qemu_m4_test.py
    python scripts/qemu_m4_test.py --ai --timeout 1200 --command-timeout 600

The default path loads the real model but performs no inference. Each run owns
its VM, QMP pipes, boot image, serial log and screenshots under build/m4-test/.
It never uses the interactive VM's serial.log, ESP, or TCP monitor port.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from qemu_accel import accel_args
from build_boot_image import build_image, files_from_esp, selected_boot_files


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = re.compile(
    r"\[m4\] receipt=(\d+) request=(\d+) op=(\d+) status=(\d+) "
    r"mode=(\d+) revision=(\d+)"
)
RECEIPT_FIELDS = ("receipt", "request", "op", "status", "mode", "revision")
FATAL = re.compile(r"KERNEL PANIC|EXCEPCI[OÓ]N|\[panic\]|!!!! X64 Exception Type", re.IGNORECASE)
DONE = "[model] m4: command done"
KEYS = {
    " ": "spc", "?": "shift-slash", "/": "slash", ",": "comma", ".": "dot",
    "!": "shift-1", "'": "apostrophe", '"': "shift-apostrophe", "-": "minus",
    "_": "shift-minus", ":": "shift-semicolon", ";": "semicolon",
    "=": "equal", "+": "shift-equal", "(": "shift-9", ")": "shift-0",
    "[": "bracket_left", "]": "bracket_right", "@": "shift-2",
}


class TestFailure(RuntimeError):
    """A VM failure, timeout or assertion that must return a nonzero status."""


def keyseq(text: str) -> list[str]:
    keys = []
    for char in text:
        if char in KEYS:
            keys.append(KEYS[char])
        elif "A" <= char <= "Z":
            keys.append("shift-" + char.lower())
        elif "a" <= char <= "z" or "0" <= char <= "9":
            keys.append(char)
        else:
            raise ValueError(f"Unsupported keyboard character: {char!r}")
    return keys


def receipts(text: str) -> list[dict[str, int]]:
    return [dict(zip(RECEIPT_FIELDS, map(int, match.groups())))
            for match in RECEIPT.finditer(text)]


def halo_sequence(text: str) -> list[int]:
    return [int(value) for value in re.findall(r"^\[halo\] estado=(\d+)\r?$", text, re.MULTILINE)]


def presents(text: str) -> list[dict[str, int]]:
    return [{"op": int(op), "receipt": int(receipt)} for op, receipt in
            re.findall(r"^\[present\] op=(\d+) recibo=(\d+)\r?$", text, re.MULTILINE)]


def firmware_path(value: str | None) -> Path:
    candidates = [value or os.environ.get("OVMF", ""),
                  "C:/Program Files/qemu/share/edk2-x86_64-code.fd",
                  "/usr/share/OVMF/OVMF_CODE_4M.fd",
                  "/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/ovmf/OVMF.fd"]
    for candidate in candidates:
        if not candidate:
            continue
        if os.name == "nt" and re.match(r"^/[a-zA-Z]/", candidate):
            candidate = candidate[1].upper() + ":" + candidate[2:]
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    raise TestFailure("OVMF firmware not found; set OVMF or pass --ovmf PATH")


def qemu_path(value: str | None) -> str:
    if value:
        resolved = shutil.which(value)
        if resolved:
            return resolved
        raise TestFailure(f"QEMU executable not found: {value}")
    resolved = shutil.which("qemu-system-x86_64")
    fallback = Path("C:/Program Files/qemu/qemu-system-x86_64.exe")
    if resolved:
        return resolved
    if fallback.is_file():
        return str(fallback)
    raise TestFailure("qemu-system-x86_64 not found; source scripts/env.sh or pass --qemu")


class QemuSession:
    def __init__(self, args: argparse.Namespace, suite: str = "m4"):
        self.args = args
        self.qemu = qemu_path(args.qemu)
        self.ovmf = firmware_path(args.ovmf)
        base = ROOT / "build" / f"{suite}-test"
        base.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=base))
        self.serial_path = self.directory / "serial.log"
        self.stderr_path = self.directory / "qemu.stderr.log"
        self.proc: subprocess.Popen | None = None
        self.stderr_file = None
        self.messages: queue.Queue = queue.Queue()
        self.sequence = 0
        self.started = time.monotonic()
        self.deadline = self.started + args.timeout
        self.result: dict = {"suite": suite, "passed": False, "commands": [],
                             "directory": str(self.directory), "inputs": []}

    def prepare(self):
        if getattr(self.args,'workspace_disk',None):
            from workspace_disk import ensure_disk
            disk=Path(self.args.workspace_disk).resolve()
            self.result['boot_image']=ensure_disk(disk,Path(self.args.model),getattr(self.args,'config',None))
            self.result['inputs']=self.result['boot_image']['boot_files']
            shutil.copy2(self.ovmf,self.directory/'firmware.fd')
            return
        boot = self.directory / "esp" / "boot"
        efi = self.directory / "esp" / "EFI" / "BOOT"
        boot.mkdir(parents=True)
        efi.mkdir(parents=True)
        config = ROOT / "kernel" / "limine.conf"
        if getattr(self.args, "model", None):
            files = selected_boot_files(Path(self.args.model), getattr(self.args, "config", None))
            for name, source in files.items():
                destination = self.directory / "esp" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(source, bytes):
                    destination.write_bytes(source)
                else:
                    shutil.copy2(source, destination)
            shutil.copy2(self.ovmf, self.directory / "firmware.fd")
            snapshot = build_image(self.directory / "disk.img", files_from_esp(self.directory / "esp"))
            self.result["boot_image"] = snapshot
            self.result["inputs"] = snapshot["files"]
            return
        if getattr(self.args, "config", None):
            config = Path(self.args.config)
        content = config.read_text(encoding="utf-8")
        inputs = [(ROOT / "third_party" / "limine" / "BOOTX64.EFI", efi / "BOOTX64.EFI"),
                  (config, boot / "limine.conf"), (self.ovmf, self.directory / "firmware.fd")]
        names = re.findall(r"^\s*(?:module_path|path):\s*boot\(\):/boot/([^\s]+)\s*$",
                           content, re.MULTILINE)
        if "kernel.elf" not in names:
            raise TestFailure("limine.conf does not reference /boot/kernel.elf")
        for name in names:
            if Path(name).name != name or "\\" in name:
                raise TestFailure(f"Unsupported boot file path: {name}")
            if name == "kernel.elf":
                source = ROOT / "build" / name
            elif name.endswith(".bin"):
                source = ROOT / "build" / "user" / name
            elif name.endswith(".gguf"):
                source = ROOT / "models" / name
            else:
                raise TestFailure(f"Unsupported boot module: {name}")
            inputs.append((source, boot / name))
        for source, destination in inputs:
            if not source.is_file():
                raise TestFailure(f"Missing boot input: {source}; build with make esp first")
            shutil.copy2(source, destination)
            stat = source.stat()
            digest = hashlib.sha256()
            with destination.open("rb") as copied:
                for block in iter(lambda: copied.read(4 * 1024 * 1024), b""):
                    digest.update(block)
            self.result["inputs"].append({"source": str(source), "bytes": stat.st_size,
                                           "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()})
        self.result["boot_image"] = build_image(self.directory / "disk.img", files_from_esp(self.directory / "esp"))

    def start(self):
        self.prepare()
        self.result["preparation_elapsed_s"] = round(time.monotonic() - self.started, 3)
        accelerator = accel_args(self.args.accel)
        if getattr(self.args, "cpu", None):
            accelerator[accelerator.index("-cpu") + 1] = self.args.cpu
        command = [self.qemu, "-M", "q35", "-m", getattr(self.args, "memory", "4G"), *accelerator,
                   "-drive", "if=pflash,format=raw,readonly=on,file=firmware.fd",
                   "-drive", "format=raw,file=disk.img", "-serial", "file:serial.log",
                   "-qmp", "stdio", "-monitor", "none", "-display", getattr(self.args, "display", "none"),
                   "-no-reboot", "-no-shutdown"]
        if getattr(self.args,'workspace_disk',None):
            at=command.index('format=raw,file=disk.img')
            command[at]='if=none,id=osdisk,format=raw,cache=writeback,file='+str(Path(self.args.workspace_disk).resolve())
            command += ['-device','virtio-blk-pci,drive=osdisk,disable-modern=on,serial=ERLANOS-D1']
        self.result["qemu_command"] = command
        self.stderr_file = self.stderr_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(command, cwd=self.directory, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.stderr_file,
                                     text=True, encoding="utf-8", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.result["qemu_started_elapsed_s"] = round(time.monotonic() - self.started, 3)
        self.result["qemu_pid"] = self.proc.pid
        (self.directory / "qemu.pid").write_text(str(self.proc.pid) + "\n", encoding="ascii")
        threading.Thread(target=self._reader, daemon=True).start()
        greeting = self._message(15)
        if "QMP" not in greeting:
            raise TestFailure(f"Invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")
        print(f"[test] artifacts: {self.directory}", flush=True)

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.messages.put(json.loads(line))
                except json.JSONDecodeError:
                    self.messages.put({"protocol_error": line.rstrip()})
        finally:
            self.messages.put(None)

    def _message(self, timeout: float):
        try:
            message = self.messages.get(timeout=max(0.01, timeout))
        except queue.Empty as error:
            raise TestFailure("QMP response timed out") from error
        if message is None:
            raise TestFailure(f"QMP connection closed; stderr: {self.stderr_text()[-2000:]}")
        if "protocol_error" in message:
            raise TestFailure(f"QMP protocol error: {message['protocol_error']}")
        return message

    def execute(self, command: str, arguments: dict | None = None, timeout: float = 10):
        if self.proc is None or self.proc.poll() is not None:
            raise TestFailure(f"QEMU is not running; stderr: {self.stderr_text()[-2000:]}")
        self.sequence += 1
        request = {"execute": command, "id": self.sequence}
        if arguments is not None:
            request["arguments"] = arguments
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self._message(deadline - time.monotonic())
            if response.get("id") != self.sequence:
                continue  # asynchronous QMP event
            if "error" in response:
                raise TestFailure(f"QMP {command}: {response['error']}")
            return response.get("return")
        raise TestFailure(f"QMP {command} response timed out")

    def log(self) -> str:
        try:
            return self.serial_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def stderr_text(self) -> str:
        try:
            return self.stderr_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def check_health(self):
        text = self.log()
        stop_request = self.directory / "stop.request"
        if stop_request.is_file():
            raise TestFailure("Stopped by test controller: " + stop_request.read_text(encoding="utf-8", errors="replace").strip())
        match = FATAL.search(text)
        if match:
            raise TestFailure(f"Kernel failure in serial log: {text[match.start():match.start()+300]}")
        if self.proc is not None and self.proc.poll() is not None:
            raise TestFailure(f"QEMU exited ({self.proc.returncode}); stderr: {self.stderr_text()[-2000:]}")
        if time.monotonic() >= self.deadline:
            raise TestFailure(f"Global timeout ({self.args.timeout}s)")
        return text

    def wait_for(self, predicate, description: str, timeout: float | None = None):
        deadline = self.deadline if timeout is None else min(self.deadline, time.monotonic() + timeout)
        last_progress = time.monotonic()
        while True:
            text = self.check_health()
            if predicate(text):
                return text
            if time.monotonic() >= deadline:
                raise TestFailure(f"Timeout waiting for {description}")
            if time.monotonic() - last_progress >= 30:
                print(f"[test] waiting for {description} ({int(time.monotonic()-self.started)}s elapsed)", flush=True)
                last_progress = time.monotonic()
            time.sleep(0.2)

    def send_key(self, key: str):
        self.check_health()
        self.execute("human-monitor-command", {"command-line": f"sendkey {key} 30"})
        time.sleep(0.08)

    def type_text(self, text: str):
        for key in keyseq(text):
            self.send_key(key)

    def type_line(self, text: str):
        self.type_text(text)
        self.send_key("ret")

    def type_multiline(self, text: str):
        """Enter a request using physical Shift+Enter keys for embedded LFs."""
        lines = text.split("\n")
        for line in lines:
            keyseq(line)  # Validate the whole request before changing the input.
        before = self.log().count("[model] chat: begin")
        for index, line in enumerate(lines):
            if index:
                self.send_key("shift-ret")
                if self.log().count("[model] chat: begin") != before:
                    raise TestFailure("Shift+Enter submitted the request instead of inserting a newline")
            self.type_text(line)
        self.send_key("ret")

    def command(self, text: str) -> str:
        before = self.log()
        count = before.count(DONE)
        print(f"[test] {text}", flush=True)
        started = time.monotonic()
        self.type_line(text)
        after = self.wait_for(lambda log: log.count(DONE) > count,
                              f"completion of {text}", self.args.command_timeout)
        new = after[len(before):]
        self.result["commands"].append({"input": text, "elapsed_s": round(time.monotonic()-started, 3),
                                        "receipts": receipts(new), "serial": new})
        return new

    def capture(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.execute("screendump", {"filename": "screen.ppm"})
        self.result["screenshot"] = str(self.directory / "screen.ppm")
        try:
            from PIL import Image
        except ImportError:
            return
        with Image.open(self.directory / "screen.ppm") as screenshot:
            screenshot.save(self.directory / "screen.png")
        self.result["screenshot"] = str(self.directory / "screen.png")

    def finish(self, failure: Exception | None):
        if failure is not None:
            self.result["failure"] = str(failure)
        try:
            self.capture()
        except Exception as error:
            self.result["capture_error"] = str(error)
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self.execute("quit", timeout=3)
                except (TestFailure, BrokenPipeError, OSError):
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
            self.result["qemu_exit_code"] = self.proc.returncode
            if self.proc.returncode != 0 and failure is None:
                self.result["failure"] = f"QEMU exited with status {self.proc.returncode}"
            for pipe in (self.proc.stdin, self.proc.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass  # a failed/closed QMP pipe may reject the final flush
        if self.stderr_file is not None:
            self.stderr_file.close()
        self.result["elapsed_s"] = round(time.monotonic() - self.started, 3)
        self.result["stderr"] = self.stderr_text()
        self.result["passed"] = "failure" not in self.result
        (self.directory / "result.json").write_text(json.dumps(self.result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tail = [line for line in self.log().splitlines()
                if line and not line.startswith(("  0x", "[pit] "))][-45:]
        print("\n".join(tail), flush=True)
        if self.result["stderr"].strip():
            print(f"[test] QEMU stderr:\n{self.result['stderr'].strip()}", flush=True)
        print(f"[test] {'PASS' if self.result['passed'] else 'FAIL'}: {self.directory / 'result.json'}", flush=True)
        if "failure" in self.result:
            print(f"[test] {self.result['failure']}", file=sys.stderr, flush=True)
        return 0 if self.result["passed"] else 1


def common_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--timeout", type=int, default=600, help="Total VM timeout in seconds")
    parser.add_argument("--command-timeout", type=int, default=120, help="Timeout per slash command")
    parser.add_argument("--qemu", help="QEMU executable path")
    parser.add_argument("--ovmf", help="OVMF code firmware path")
    parser.add_argument("--accel", choices=("tcg", "kvm", "whpx"), default="tcg")
    parser.add_argument("--cpu", help="Override the QEMU CPU model/features")
    parser.add_argument("--memory", default="4G", help="Guest RAM, in QEMU syntax (default: 4G)")
    parser.add_argument("--model", type=Path, help="Select one GGUF instead of the configured model")
    parser.add_argument("--config", type=Path, help="Private Limine configuration or template for --model")


def check_receipt(found: list[dict[str, int]], expected: dict, previous: dict | None):
    if len(found) != 1:
        raise TestFailure(f"Expected exactly one broker receipt, got {found}")
    receipt = found[0]
    for key, value in expected.items():
        if receipt[key] != value:
            raise TestFailure(f"Receipt {key}: expected {value}, got {receipt}")
    if receipt["receipt"] <= 0 or receipt["request"] <= 0:
        raise TestFailure(f"Receipt/request ID must be positive: {receipt}")
    if previous is not None and (receipt["receipt"] <= previous["receipt"]
                                 or receipt["request"] <= previous["request"]):
        raise TestFailure(f"Receipt/request IDs must increase: {previous} -> {receipt}")
    return receipt


def run_m4(vm: QemuSession, ai_cases: list[tuple[str, int]]):
    vm.wait_for(lambda log: "[system] system: listo" in log
                and "[model] model: listo" in log and "[model] m4: ready" in log,
                "system broker and model input readiness")
    if halo_sequence(vm.log().split("[model] m4: ready", 1)[0]) != [0]:
        raise TestFailure("Initial halo trace must contain exactly [0] before m4: ready")
    previous = None
    cases = [("/estado", 1, 0, 0), ("/modo foco", 2, 1, 1), ("/modo foco", 2, 1, 1),
             ("/estado", 1, 1, 1), ("/modo normal", 3, 0, 2), ("/panel", 4, 0, 2)]
    for command, op, mode, revision in cases:
        text = vm.command(command)
        previous = check_receipt(receipts(text), {"op": op, "status": 0, "mode": mode,
                                                  "revision": revision}, previous)
        expected_halo = [1, 2, 0] if op == 1 else [1, 2, 3, 0, 0]
        if halo_sequence(text) != expected_halo:
            raise TestFailure(f"{command}: halo {halo_sequence(text)} != {expected_halo}")
        expected_present = [] if op == 1 else [{"op": op, "receipt": previous["receipt"]}]
        if presents(text) != expected_present:
            raise TestFailure(f"{command}: presents {presents(text)} != {expected_present}")
    text = vm.command("/noexiste")
    if receipts(text):
        raise TestFailure("Unknown command unexpectedly created a broker receipt")
    if halo_sequence(text) != [1, 2, 0] or presents(text):
        raise TestFailure("Unknown command must have halo [1,2,0] and no present")
    text = vm.command("/prueba")
    if "[model] m4: selftest PASS" not in text:
        raise TestFailure("M4 negative ABI/capability selftest did not PASS")
    if halo_sequence(text) != [1, 2, 0] or presents(text):
        raise TestFailure("/prueba must have halo [1,2,0] and no present")
    for operation in ("halo acting", "halo authorization", "halo cloud"):
        if not re.search(r"^\[cap\] DENEGADO: hilo \d+ 'model' intento " + operation +
                         r" con slot 3\r?$", text, re.MULTILINE):
            raise TestFailure(f"Missing model denial for {operation}")
    negative = receipts(text)
    if not {1, 2, 3, 4, 5}.issubset({item["status"] for item in negative}):
        raise TestFailure(f"Missing negative ABI rejection statuses: {negative}")
    if any(item["mode"] != 0 or item["revision"] != 2 for item in negative):
        raise TestFailure(f"Negative ABI test unexpectedly mutated system state: {negative}")
    if "[init] m4: readonly PASS" not in vm.log():
        raise TestFailure("Read-only System capability test did not PASS")
    previous = negative[-1]
    mode, revision = 0, 2
    for query, op in ai_cases:
        text = vm.command("/ai " + query)
        plans = re.findall(r"\[model\] m4: plan=(\d+)(?:\r?\n|$)", text)
        if plans != [str(op)]:
            raise TestFailure(f"AI selected {plans}, expected operation {op} for {query!r}")
        next_mode = 1 if op == 2 else 0 if op == 3 else mode
        if next_mode != mode:
            revision += 1
        mode = next_mode
        previous = check_receipt(receipts(text), {"op": op, "status": 0,
                                                  "mode": mode, "revision": revision}, previous)
        expected_halo = [1, 2, 2, 0] if op == 1 else [1, 2, 2, 3, 0, 0]
        if halo_sequence(text) != expected_halo:
            raise TestFailure(f"/ai {query}: halo {halo_sequence(text)} != {expected_halo}")
        expected_present = [] if op == 1 else [{"op": op, "receipt": previous["receipt"]}]
        if presents(text) != expected_present:
            raise TestFailure(f"/ai {query}: presents {presents(text)} != {expected_present}")
    vm.check_health()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_arguments(parser)
    parser.add_argument("--ai", action="store_true", help="Also infer one real system-status plan (slow under TCG)")
    parser.add_argument("--ai-query", action="append", default=[], help="Custom planner query; repeat with matching --ai-expected")
    parser.add_argument("--ai-expected", action="append", type=int, choices=(1, 2, 3, 4), default=[],
                        help="Expected operation for each --ai-query (1=status, 2=focus, 3=normal, 4=panel)")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.command_timeout <= 0:
        parser.error("timeouts must be positive")
    if len(args.ai_query) != len(args.ai_expected):
        parser.error("each --ai-query needs one --ai-expected")
    ai_cases = list(zip(args.ai_query, args.ai_expected))
    if args.ai and not ai_cases:
        ai_cases = [("Show system status", 1)]
    for query, _ in ai_cases:
        if not query or len(query) > 236:
            parser.error("AI queries must contain 1 to 236 characters")
        try:
            keyseq(query)
        except ValueError as error:
            parser.error(str(error))
    vm = None
    failure = None
    try:
        vm = QemuSession(args)
        vm.start()
        run_m4(vm, ai_cases)
    except KeyboardInterrupt:
        failure = TestFailure("Interrupted by user")
    except Exception as error:
        failure = error
    if vm is not None:
        return vm.finish(failure)
    print(f"[test] FAIL: {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
