#!/usr/bin/env python3
"""Exercise the installed CUDA driver with tiny fixed kernels; not an ErlanOS driver.

No toolkit, PyTorch, installer, clock changes or firmware changes. Outputs include
original PTX, JIT CUBIN, exact CPU comparisons and a trace of public API calls.
This API trace is NOT an MMIO/command-buffer trace. Driver context overhead is
separate from the small explicitly allocated buffers reported here.
"""
from __future__ import annotations
import argparse
import ctypes as C
import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
P = C.c_void_p
U = C.c_uint
D = C.c_uint64
I = C.c_int
Z = C.c_size_t


class Driver:
    def __init__(self):
        if os.name == "nt":
            self.lib = C.WinDLL(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/nvcuda.dll"))
        else:
            self.lib = C.CDLL("libcuda.so.1")
        self.trace = []
        declarations = {
            "cuInit": [U], "cuDriverGetVersion": [C.POINTER(I)],
            "cuDeviceGetCount": [C.POINTER(I)], "cuDeviceGet": [C.POINTER(I), I],
            "cuDeviceGetName": [P, I, I],
            "cuDeviceComputeCapability": [C.POINTER(I), C.POINTER(I), I],
            "cuDeviceTotalMem_v2": [C.POINTER(Z), I],
            "cuCtxCreate_v2": [C.POINTER(P), U, I], "cuCtxDestroy_v2": [P],
            "cuCtxSynchronize": [],
            "cuLinkCreate_v2": [U, P, P, C.POINTER(P)],
            "cuLinkAddData_v2": [P, I, P, Z, C.c_char_p, U, P, P],
            "cuLinkComplete": [P, C.POINTER(P), C.POINTER(Z)], "cuLinkDestroy": [P],
            "cuModuleLoadData": [C.POINTER(P), P], "cuModuleUnload": [P],
            "cuModuleGetFunction": [C.POINTER(P), P, C.c_char_p],
            "cuMemAlloc_v2": [C.POINTER(D), Z], "cuMemFree_v2": [D],
            "cuMemcpyHtoD_v2": [D, P, Z], "cuMemcpyDtoH_v2": [P, D, Z],
            "cuLaunchKernel": [P, U, U, U, U, U, U, U, P, C.POINTER(P), P],
            "cuEventCreate": [C.POINTER(P), U], "cuEventDestroy_v2": [P],
            "cuEventRecord": [P, P], "cuEventElapsedTime": [C.POINTER(C.c_float), P, P],
            "cuGetErrorName": [I, C.POINTER(C.c_char_p)],
        }
        for name, args in declarations.items():
            fn = getattr(self.lib, name)
            fn.argtypes = args
            fn.restype = I

    def call(self, name, *args):
        start = time.perf_counter()
        status = getattr(self.lib, name)(*args)
        self.trace.append({"function": name, "status": status,
                           "host_ms": round((time.perf_counter() - start) * 1000, 6)})
        if status:
            msg = C.c_char_p()
            self.lib.cuGetErrorName(status, C.byref(msg))
            raise RuntimeError(f"{name}: {status} {(msg.value or b'unknown').decode()}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_case(driver, module, output, kernel, n):
    # Integer-valued f32 inputs keep all intermediate arithmetic exact here.
    count = n if kernel == "vector_add" else n * n
    a = [float((i * 7) % 17 - 8) for i in range(count)]
    b = [float((i * 3) % 13 - 6) for i in range(count)]
    if kernel == "vector_add":
        expected = [x + y for x, y in zip(a, b)]
    else:
        expected = [sum(a[row*n+k] * b[k*n+col] for k in range(n))
                    for row in range(n) for col in range(n)]
    buffers = []
    events = []
    try:
        hosts = [(C.c_float * count)(*a), (C.c_float * count)(*b),
                 (C.c_float * (count + 16))(*([123456.0] * (count + 16)))]
        for host in hosts:
            device = D()
            driver.call("cuMemAlloc_v2", C.byref(device), C.sizeof(host))
            buffers.append(device)
            driver.call("cuMemcpyHtoD_v2", device, host, C.sizeof(host))
        roundtrip = (C.c_float * count)()
        driver.call("cuMemcpyDtoH_v2", roundtrip, buffers[0], C.sizeof(roundtrip))
        if list(roundtrip) != a:
            raise AssertionError("Host/device round-trip mismatch")
        func = P()
        driver.call("cuModuleGetFunction", C.byref(func), module, kernel.encode())
        for _ in range(2):
            event = P()
            driver.call("cuEventCreate", C.byref(event), 0)
            events.append(event)
        params = [*buffers, U(n)]
        pointers = (P * len(params))(*[C.cast(C.byref(x), P) for x in params])
        driver.call("cuEventRecord", events[0], None)
        start = time.perf_counter()
        driver.call("cuLaunchKernel", func, (count + 127) // 128, 1, 1,
                    128, 1, 1, 0, None, pointers, None)
        driver.call("cuEventRecord", events[1], None)
        driver.call("cuCtxSynchronize")
        wall_ms = (time.perf_counter() - start) * 1000
        elapsed = C.c_float()
        driver.call("cuEventElapsedTime", C.byref(elapsed), events[0], events[1])
        driver.call("cuMemcpyDtoH_v2", hosts[2], buffers[2], C.sizeof(hosts[2]))
        actual = list(hosts[2])[:count]
        guard = list(hosts[2])[count:] == [123456.0] * 16
        errors = sum(x != y for x, y in zip(actual, expected))
        stem = f"{kernel}-{n}"
        raw_a = bytes(hosts[0]); raw_b = bytes(hosts[1])
        raw_out = bytes(hosts[2])[:count * 4]
        raw_expected = struct.pack(f"<{count}f", *expected)
        for label, data in (("a", raw_a), ("b", raw_b), ("actual", raw_out), ("expected", raw_expected)):
            (output / f"{stem}-{label}.f32").write_bytes(data)
        result = {"kernel": kernel, "n": n, "outputs": count,
                  "mismatches": errors, "trailing_guard_unchanged": guard,
                  "roundtrip_exact": True, "gpu_event_ms": elapsed.value,
                  "launch_and_sync_host_ms": round(wall_ms, 6),
                  "explicit_device_buffer_bytes": sum(C.sizeof(h) for h in hosts),
                  "expected_sha256": digest(raw_expected), "actual_sha256": digest(raw_out),
                  "passed": errors == 0 and guard and raw_out == raw_expected}
        return result
    finally:
        for event in reversed(events):
            driver.call("cuEventDestroy_v2", event)
        for device in reversed(buffers):
            driver.call("cuMemFree_v2", device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT / "build/gpu-lab" /
              datetime.datetime.now().strftime("cuda-%Y%m%d-%H%M%S")).resolve()
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("output must be inside this workspace's build directory")
    output.mkdir(parents=True, exist_ok=True)
    result = {"passed": False, "native_erlanos_gpu_support": False,
              "backend": "installed CUDA driver", "os": sys.platform,
              "trace_scope": "public Driver API only, not MMIO or GPU command packets",
              "timing_scope": "tiny correctness tests; not an optimized model benchmark",
              "cases": []}
    driver = None
    context = P(); link = P(); module = P()
    try:
        driver = Driver()
        driver.call("cuInit", 0)
        count = I(); device = I(); version = I(); major = I(); minor = I(); total = Z()
        driver.call("cuDeviceGetCount", C.byref(count))
        if args.device < 0 or args.device >= count.value:
            raise ValueError("CUDA device ordinal out of range")
        driver.call("cuDeviceGet", C.byref(device), args.device)
        name = C.create_string_buffer(256)
        driver.call("cuDeviceGetName", name, len(name), device)
        driver.call("cuDeviceComputeCapability", C.byref(major), C.byref(minor), device)
        driver.call("cuDeviceTotalMem_v2", C.byref(total), device)
        driver.call("cuDriverGetVersion", C.byref(version))
        result.update(device=name.value.decode(), compute_capability=[major.value, minor.value],
                      device_memory_bytes=total.value, cuda_driver_api_version=version.value)
        if (major.value, minor.value) < (6, 1):
            raise ValueError("Probe PTX requires compute capability 6.1 or later")
        driver.call("cuCtxCreate_v2", C.byref(context), 0, device)
        ptx = Path(__file__).with_name("kernels.ptx").read_bytes()
        ptx_buf = C.create_string_buffer(ptx)
        driver.call("cuLinkCreate_v2", 0, None, None, C.byref(link))
        # CU_JIT_INPUT_PTX=1; include the terminating NUL per the Driver API.
        driver.call("cuLinkAddData_v2", link, 1, ptx_buf, len(ptx) + 1,
                    b"erlanos-reference.ptx", 0, None, None)
        cubin_ptr = P(); cubin_size = Z()
        driver.call("cuLinkComplete", link, C.byref(cubin_ptr), C.byref(cubin_size))
        if not 0 < cubin_size.value < 4 * 1024 * 1024:
            raise ValueError("Unexpected JIT output size")
        cubin = C.string_at(cubin_ptr, cubin_size.value)
        (output / "kernels.ptx").write_bytes(ptx)
        (output / "kernels.cubin").write_bytes(cubin)
        result.update(ptx_sha256=digest(ptx), cubin_sha256=digest(cubin), cubin_bytes=len(cubin))
        driver.call("cuModuleLoadData", C.byref(module), cubin_ptr)
        driver.call("cuLinkDestroy", link); link = P()
        for n in (1, 31, 257, 4097):
            result["cases"].append(run_case(driver, module, output, "vector_add", n))
        for n in (1, 7, 17, 32, 64):
            result["cases"].append(run_case(driver, module, output, "matrix_mul", n))
        result["passed"] = all(c["passed"] for c in result["cases"])
    except Exception as error:
        result["error"] = str(error)
    finally:
        if driver:
            for name, resource in (("cuModuleUnload", module), ("cuLinkDestroy", link),
                                   ("cuCtxDestroy_v2", context)):
                if resource.value:
                    try:
                        driver.call(name, resource)
                    except Exception as error:
                        result["passed"] = False
                        result.setdefault("cleanup_errors", []).append(str(error))
            result["driver_api_calls"] = driver.trace
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "driver_api_calls"}, indent=2))
    print(f"Artifacts: {output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
