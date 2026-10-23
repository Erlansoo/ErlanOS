#!/usr/bin/env python3
"""Verify the coffee canvas and UI-01 from a private QEMU VM and exact font pixels.

Only slash commands are submitted. No chat/planner inference is requested;
the service still performs its normal model load and warmup before readiness.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

from qemu_m4_test import (QemuSession, TestFailure, common_arguments, keyseq,
                          receipts, DONE, halo_sequence, presents)

ROOT = Path(__file__).resolve().parents[1]
# espejo de console.e: fixed glyph and panel grid, never a display resolution.
GLYPH_W = 8
GLYPH_H = 16
PANEL_TEXT_DX = 20
PANEL_TEXT_DY = 24
PANEL_ROW_H = 20
FG = (0xd8, 0xd8, 0xde)
FOCUS_BG = (0x24, 0x20, 0x18)
FOCUS_ACCENT = (0xe8, 0xc0, 0x80)


class UiSession(QemuSession):
    def __init__(self, args):
        super().__init__(args, suite="ui")
        self.result["ui_checks"] = []
        self.result["frames"] = []

    def check(self, name, condition, evidence):
        self.result["ui_checks"].append({"name": name, "passed": bool(condition),
                                         "evidence": evidence})
        if not condition:
            raise TestFailure(f"{name}: {evidence}")

    def start(self):
        try:
            from PIL import Image, ImageChops
        except ImportError as error:
            raise TestFailure("UI-01 requires PIL/Pillow for exact PNG pixel verification") from error
        self.Image, self.ImageChops = Image, ImageChops
        self.build_inputs = []
        for path in [ROOT / "build/kernel.elf", *[ROOT / "build/user" / (name + ".bin")
                     for name in ("init", "echo", "system", "model")]]:
            self.build_inputs.append({"source": str(path), "bytes": path.stat().st_size,
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.result["build_inputs_before_start"] = self.build_inputs
        super().start()
        for expected in self.build_inputs:
            name = Path(expected["source"]).name
            found = [item for item in self.result["inputs"]
                     if Path(item.get("path", item.get("source", ""))).name == name]
            self.check("boot input " + name, len(found) == 1 and
                       found[0]["sha256"] == expected["sha256"] and
                       found[0]["bytes"] == expected["bytes"],
                       {"compiled": expected, "boot_copy": found})

    def geometry(self):
        log = self.log()
        framebuffer = re.search(r"framebuffer: (\d+)x(\d+)", log)
        grid = re.search(r"\[console\] (\d+)x(\d+) caracteres", log)
        layout = re.search(r"\[console\] geometria header=(\d+) margen=(\d+) panel_x=(\d+) ancho=(\d+) alto=(\d+)", log)
        self.check("serial geometry exists", bool(framebuffer and grid and layout),
                   "framebuffer, [console] caracteres and [console] geometria in serial.log")
        self.width, self.height = map(int, framebuffer.groups())
        self.cols, self.rows = map(int, grid.groups())
        self.header, self.margin, self.panel_x, width, height = map(int, layout.groups())
        self.check("serial geometry agrees", (width, height) == (self.width, self.height)
                   and self.cols > 0 and self.rows > 0 and
                   self.margin + self.cols * GLYPH_W <= self.panel_x and
                   self.header + self.rows * GLYPH_H <= self.height,
                   [framebuffer.group(), grid.group(), layout.group()])
        self.result["geometry"] = dict(width=width, height=height, header=self.header,
                                        margin=self.margin, panel_x=self.panel_x,
                                        cols=self.cols, rows=self.rows)
        source = ROOT / "kernel/src/font8x16.h"
        groups = re.findall(r"\{\s*((?:0x[0-9a-fA-F]{2}\s*,?\s*)+)\}", source.read_text())
        glyphs = [bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", group))
                  for group in groups]
        self.check("exact font decoder", len(glyphs) == 224 and
                   all(len(glyph) == GLYPH_H for glyph in glyphs), str(source))
        self.font = {}
        for code, glyph in enumerate(glyphs, 32):
            self.font.setdefault(glyph, chr(code))
        self.result["font_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()

    def snapshot(self, label):
        self.check_health()
        ppm, png = self.directory / (label + ".ppm"), self.directory / (label + ".png")
        self.execute("screendump", {"filename": ppm.name})
        with self.Image.open(ppm) as screenshot:
            frame = screenshot.convert("RGB")
            frame.save(png)
        self.result["frames"].append({"label": label, "ppm": str(ppm), "png": str(png),
                                      "width": frame.width, "height": frame.height})
        self.check(label + " dimensions", frame.size == (self.width, self.height), str(png))
        return frame

    def decode(self, frame, x, y, count, background):
        if x < 0 or y < 0 or x + count * GLYPH_W > self.width or y + GLYPH_H > self.height:
            raise TestFailure(f"Decoder region is outside framebuffer: {(x, y, count)}")
        text = []
        for col in range(count):
            rows = []
            for row in range(GLYPH_H):
                bits = 0
                for bit in range(GLYPH_W):
                    point = (x + col * GLYPH_W + bit, y + row)
                    bg = background.getpixel(point) if hasattr(background, "getpixel") else background
                    if frame.getpixel(point) != bg:
                        bits |= 1 << (GLYPH_W - 1 - bit)
                rows.append(bits)
            text.append(self.font.get(bytes(rows), "?"))
        return "".join(text)

    def text_rows(self, frame):
        return [self.decode(frame, self.margin, self.header + row * GLYPH_H, self.cols, self.canvas)
                for row in range(self.rows)]

    def panel_row(self, frame, row):
        x, y = self.panel_x + PANEL_TEXT_DX, self.header + PANEL_TEXT_DY + PANEL_ROW_H * row
        self.check(f"panel row {row} fits", y + GLYPH_H <= self.height - 12,
                   {"row": row, "bottom": y + GLYPH_H, "limit": self.height - 12})
        count = min(36, (self.width - 12 - x) // GLYPH_W)
        background = frame.getpixel((self.panel_x + 8, self.header + 8))
        return self.decode(frame, x, y, count, background).rstrip()

    def idle_header(self, frame, label):
        cx, cy = self.width // 2, self.header // 2
        centers = [(cx - 30, cy), (cx, cy), (cx + 30, cy)]
        self.check(label + " idle dots", all(frame.getpixel(p) == FG for p in centers),
                   {"frame": label, "samples": [(p, frame.getpixel(p)) for p in centers]})
        # The only bright pixels in the entire header are the three radius-5 dots.
        bright = {(x, y) for y in range(self.header) for x in range(self.width)
                  if max(frame.getpixel((x, y))) > 160}
        dots = {(x + dx, y + dy) for x, y in centers for dy in range(-5, 6)
                for dx in range(-5, 6) if dx * dx + dy * dy <= 25}
        self.check(label + " only three dots", bright == dots,
                   {"frame": label, "bright_pixels": len(bright), "dot_pixels": len(dots)})

    def clean_canvas(self, frame):
        body = frame.crop((0, self.header, self.width, self.height))
        # Detect banners, prompts, panel edges and seams without copying the
        # renderer's formula: a clean canvas has low contrast between neighbours.
        w, h = body.size
        dx = self.ImageChops.difference(body.crop((1, 0, w, h)), body.crop((0, 0, w - 1, h)))
        dy = self.ImageChops.difference(body.crop((0, 1, w, h)), body.crop((0, 0, w, h - 1)))
        maximum = max(high for low, high in (*dx.getextrema(), *dy.getextrema()))
        self.check("startup has no text or panel seams", maximum <= 3,
                   {"maximum_neighbour_contrast": maximum})
        center = frame.getpixel((self.width // 2, self.height // 2))
        corner = frame.getpixel((0, self.height - 1))
        self.check("warm coffee glow", center[0] > center[1] > center[2] and
                   center[0] - corner[0] > 60 and center[2] - corner[2] > 30,
                   {"center": center, "corner": corner})
        colors = body.getcolors(w * h)
        self.check("continuous full canvas gradient", len(colors) > 100,
                   {"colors": len(colors), "size": body.size})

    def slash(self, command, op=None):
        self.check("slash command only", command.startswith("/"), command)
        keyseq(command)
        text = self.command(command)
        self.check(command + " completed", DONE in text, text)
        expected_halo = [1, 2, 0] if op in (None, 1) else [1, 2, 3, 0, 0]
        self.check(command + " halo", halo_sequence(text) == expected_halo,
                   {"expected": expected_halo, "observed": halo_sequence(text), "serial": text})
        if op is None:
            self.check(command + " no presentation", presents(text) == [], text)
            return text, None
        found = receipts(text)
        self.check(command + " receipt", len(found) == 1 and found[0]["op"] == op and
                   found[0]["status"] == 0 and found[0]["receipt"] > 0, text)
        expected = [] if op == 1 else [{"op": op, "receipt": found[0]["receipt"]}]
        self.check(command + " presentation receipt", presents(text) == expected, text)
        return text, found[0]


def run_ui(vm):
    vm.wait_for(lambda log: "[console] geometria" in log, "canvas initialized")
    vm.geometry()
    loading = vm.snapshot("00-loading")
    vm.idle_header(loading, "00-loading")
    vm.clean_canvas(loading)
    vm.wait_for(lambda log: "[system] system: listo" in log and "[model] model: listo" in log
                and "[model] m4: ready" in log, "system broker and model input readiness")
    before_ready = vm.log().split("[model] m4: ready", 1)[0]
    vm.check("initial idle trace", halo_sequence(before_ready) == [0], before_ready)

    idle = vm.snapshot("00-idle")
    vm.canvas = idle.copy()
    vm.idle_header(idle, "00-idle")
    vm.check("loading and ready keep the same clean canvas",
             vm.ImageChops.difference(loading, idle).getbbox() is None,
             ["00-loading.png", "00-idle.png"])
    vm.send_key("esc")
    vm.send_key("backspace")
    untouched = vm.snapshot("00-empty-keys")
    vm.check("empty Escape and Backspace preserve canvas",
             vm.ImageChops.difference(idle, untouched).getbbox() is None,
             ["00-idle.png", "00-empty-keys.png"])
    prompt_row = 0

    long_command = "/" + "x" * 125
    text, _ = vm.slash(long_command)
    vm.check("unknown wrapped command has no receipt", not receipts(text), text)
    wrapped = vm.snapshot("01-wrap")
    echoed = "> " + long_command
    vm.check("wrap uses two rows", vm.cols < len(echoed) <= 2 * vm.cols and prompt_row + 1 < vm.rows,
             {"columns": vm.cols, "echo_length": len(echoed), "prompt_row": prompt_row})
    wrap_rows = vm.text_rows(wrapped)
    vm.check("exact character wrap", wrap_rows[prompt_row] == echoed[:vm.cols] and
             wrap_rows[prompt_row + 1] == echoed[vm.cols:].ljust(vm.cols),
             {"frame": "01-wrap.png", "decoded": wrap_rows[prompt_row:prompt_row+2]})
    changed = vm.ImageChops.difference(idle, wrapped).getbbox()
    vm.check("service text stays in its surface", changed is not None and
             changed[0] >= 0 and changed[1] >= vm.header and changed[2] <= vm.panel_x and changed[3] <= vm.height,
             {"frames": ["00-idle.png", "01-wrap.png"], "difference_bbox": changed})

    _, focus_receipt = vm.slash("/modo foco", 2)
    focus = vm.snapshot("02-foco")
    vm.check("focus palette", focus.getpixel((vm.panel_x, vm.header)) == FOCUS_ACCENT and
             focus.getpixel((vm.panel_x + 8, vm.header + 8)) == FOCUS_BG, "02-foco.png")
    for row, expected in [(3, "MODO FOCO"), (27, "Halo: IDLE"), (28, "Ultima accion: modo foco"),
                          (29, str(focus_receipt["receipt"])), (31, "Destino de datos: local, sin egreso")]:
        observed = vm.panel_row(focus, row)
        vm.check(f"focus panel row {row}", observed == expected,
                 {"frame": "02-foco.png", "expected": expected, "decoded": observed})

    _, panel_receipt = vm.slash("/panel", 4)
    # Temporary verification frame is retained as PPM; the four named PNGs are
    # the public milestones. No text recognition beyond exact font matching.
    vm.execute("screendump", {"filename": "panel-check.ppm"})
    with vm.Image.open(vm.directory / "panel-check.ppm") as screenshot:
        panel_frame = screenshot.convert("RGB")
    vm.check("panel verification dimensions", panel_frame.size == (vm.width, vm.height), "panel-check.ppm")
    revision = vm.panel_row(panel_frame, 5)
    vm.check("panel revision matches receipt", revision == str(panel_receipt["revision"]),
             {"frame": "panel-check.ppm", "decoded": revision, "receipt": panel_receipt})
    log = vm.log()
    audit = re.search(r"\[m4\] receipt=" + str(panel_receipt["receipt"]) + r" request=\d+ op=4 status=0[^\n]*", log)
    vm.check("panel audit exists", audit is not None, panel_receipt)
    denied_lines = re.findall(r"^\[cap\] DENEGADO:.*$", log[:audit.end()], re.MULTILINE)
    denied = len(denied_lines)
    # The audited baseline init tests BOTH Log(slot 5) and System.Write(slot 7).
    # The brief's claim of one denial predates that baseline. Count every denial
    # and require these exact two probes instead of hiding one from the metric.
    initial_probes = [re.fullmatch(r"\[cap\] DENEGADO: hilo \d+ 'init' intento (log|set_mode) con slot (\d+)\r?", line)
                      for line in denied_lines]
    vm.check("baseline init denial probes", all(initial_probes) and
             [(match[1], int(match[2])) for match in initial_probes] == [("log", 5), ("set_mode", 7)], denied_lines)
    displayed = vm.panel_row(panel_frame, 23)
    vm.check("panel denials match serial", displayed == str(denied),
             {"frame": "panel-check.ppm", "serial_count": denied, "decoded": displayed})

    text, _ = vm.slash("/prueba")
    vm.check("permission selftest", "m4: selftest PASS" in text, text)
    for operation in ("halo acting", "halo authorization", "halo cloud"):
        vm.check("model denied " + operation, bool(re.search(
            r"^\[cap\] DENEGADO: hilo \d+ 'model' intento " + operation + r" con slot 3\r?$", text, re.MULTILINE)), text)
    vm.check("model never claimed acting", 3 not in halo_sequence(text), text)

    _, normal_receipt = vm.slash("/modo normal", 3)
    final = vm.snapshot("03-final")
    vm.idle_header(final, "03-final")
    vm.check("normal idle header restored", vm.ImageChops.difference(
        idle.crop((0, 0, vm.width, vm.header)), final.crop((0, 0, vm.width, vm.header))).getbbox() is None,
        ["00-idle.png", "03-final.png"])
    for row, expected in [(27, "Halo: IDLE"), (28, "Ultima accion: modo normal"),
                          (29, str(normal_receipt["receipt"])), (31, "Destino de datos: local, sin egreso")]:
        observed = vm.panel_row(final, row)
        vm.check(f"final panel row {row}", observed == expected,
                 {"frame": "03-final.png", "expected": expected, "decoded": observed})
    vm.check("no chat or planner inference", "[model] chat: begin" not in vm.log() and
             "[model] m4: plan=" not in vm.log(), "serial.log; only slash commands")
    vm.check_health()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    common_arguments(parser)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.command_timeout <= 0:
        parser.error("timeouts must be positive")
    vm = None
    failure = None
    try:
        vm = UiSession(args)
        vm.start()
        run_ui(vm)
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
