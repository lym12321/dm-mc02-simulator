#!/usr/bin/env python3
"""Check the native Windows x64 DM-MC02 TCG cache default and overrides.

Queries actual QEMU capacity through QMP with the guest paused. Explicit
tb-size=128, tb-size=0 and another machine must retain their native behavior.
Only the Python standard library is required.
"""

import argparse
import ctypes
import json
import mmap
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time


TIMEOUT = 10
MIB = 1024**2
TAIL_BYTES = 12000


def automatic_buffer_size():
    """QEMU's Windows x64 automatic buffer is min(host RAM / 8, 1 GiB)."""
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ("total_phys", "avail_phys", "total_pagefile", "avail_pagefile",
                       "total_virtual", "avail_virtual", "avail_extended_virtual")]]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    aligned = status.total_phys // 8 // mmap.PAGESIZE * mmap.PAGESIZE
    return max(MIB, min(1024 * MIB, aligned))


def read_qmp(stream):
    line = stream.readline(65537)
    if not line or len(line) > 65536:
        raise RuntimeError("QMP disconnected or reply line exceeds 64 KiB")
    return json.loads(line)


def qmp_command(connection, stream, name, arguments=None):
    message = {"execute": name, "id": name}
    if arguments is not None:
        message["arguments"] = arguments
    connection.sendall(json.dumps(message).encode("utf-8") + b"\n")
    deadline = time.monotonic() + TIMEOUT
    for _ in range(128):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"QMP {name} exceeded {TIMEOUT} seconds")
        connection.settimeout(remaining)
        response = read_qmp(stream)
        if response.get("id") == name:
            if "return" not in response:
                raise RuntimeError(f"QMP {name} failed: {response}")
            return response["return"]
    raise RuntimeError(f"QMP {name} exceeded the event limit")


def check_capacity(qemu, case):
    case["passed"] = False
    with socket.socket() as listener, tempfile.TemporaryFile() as stderr:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(TIMEOUT)
        command = [str(qemu), "-machine", case["machine"], "-nodefaults",
                   "-display", "none", "-monitor", "none", "-serial", "none",
                   "-S", "-qmp",
                   f"tcp:127.0.0.1:{listener.getsockname()[1]},server=off"]
        if case["explicit_tb_size_mib"] is not None:
            command.extend(["-accel", f"tcg,tb-size={case['explicit_tb_size_mib']}"])
        case["command"] = command
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=stderr)
        case["qemu_pid"] = process.pid
        try:
            connection, _ = listener.accept()
            with connection, connection.makefile("rwb", buffering=0) as stream:
                connection.settimeout(TIMEOUT)
                greeting = read_qmp(stream)
                if "QMP" not in greeting:
                    raise RuntimeError(f"QMP greeting missing: {greeting}")
                qmp_command(connection, stream, "qmp_capabilities")
                info = qmp_command(connection, stream, "human-monitor-command",
                                   {"command-line": "info jit"})
                case["jit_info"] = info
                qmp_command(connection, stream, "quit")
            process.wait(timeout=TIMEOUT)
            if process.returncode:
                raise RuntimeError(f"QEMU exited with status {process.returncode}")
        finally:
            try:
                if process.poll() is None:
                    case["terminated_after_failure"] = True
                    process.terminate()
                    process.wait(timeout=TIMEOUT)
            finally:
                case["returncode"] = process.returncode
                case["reaped"] = process.returncode is not None
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - TAIL_BYTES))
                case["stderr_tail"] = stderr.read().decode("utf-8", errors="replace")
    match = re.search(r"gen code size\s+(\d+)/(\d+)", info)
    if match is None:
        raise RuntimeError(f"info jit did not report TCG capacity: {info}")
    case["used_bytes"], case["capacity_bytes"] = map(int, match.groups())
    # QEMU deducts guard/highwater space from the requested buffer.
    expected = case["expected_buffer_bytes"]
    case["passed"] = expected - 65536 <= case["capacity_bytes"] < expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True,
                        help="new JSON report path; existing reports are never replaced")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("this smoke test requires native Windows and a Windows x64 QEMU binary")
    qemu = args.qemu.resolve()
    if not qemu.is_file() or not os.access(qemu, os.X_OK):
        parser.error(f"QEMU is not executable: {qemu}")
    report_path = args.report.resolve()
    if report_path.exists():
        parser.error(f"report must be a new path: {report_path}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "qemu": str(qemu), "cases": []}
    code = 1
    with report_path.open("x", encoding="utf-8") as output:
        try:
            automatic = automatic_buffer_size()
            report["automatic_buffer_bytes"] = automatic
            for machine, explicit, expected in (("dm-mc02", None, 64 * MIB),
                                                 ("dm-mc02", 128, 128 * MIB),
                                                 ("dm-mc02", 0, automatic),
                                                 ("none", None, automatic)):
                case = {"machine": machine, "explicit_tb_size_mib": explicit,
                        "expected_buffer_bytes": expected}
                report["cases"].append(case)
                check_capacity(qemu, case)
                print(json.dumps({key: case[key] for key in
                                  ("machine", "explicit_tb_size_mib", "capacity_bytes",
                                   "passed")}), flush=True)
            if all(case["passed"] for case in report["cases"]):
                report["status"] = "passed"
                code = 0
        except (Exception, KeyboardInterrupt) as error:
            report.update(error=str(error), error_type=type(error).__name__)
            code = 130 if isinstance(error, KeyboardInterrupt) else 1
        json.dump(report, output, indent=2)
        output.write("\n")
    print(f"Windows TCG cache: {report['status']} ({report_path})", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
