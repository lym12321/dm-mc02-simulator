#!/usr/bin/env python3
"""Check native Windows QMP input batching, backpressure, and reconnection.

Runs the explicitly selected QEMU with no guest. TCP QMP exercises real
chardev reads; Windows named pipes hold in-band commands while the bounded
queue and out-of-band dispatch are checked. Only the standard library is used.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
import uuid


TIMEOUT = 10
QUIET_SECONDS = 0.25
TAIL_BYTES = 12000
MAX_LINE_BYTES = 1024 * 1024


def wire(message):
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def command(name, ident, arguments=None, oob=False):
    message = {"exec-oob" if oob else "execute": name, "id": ident}
    if arguments is not None:
        message["arguments"] = arguments
    return message


def describe(message):
    text = repr(message)
    return text if len(text) <= 1000 else text[:1000] + "..."


class QmpConnection:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()
        self.seen_ids = set()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self.connection.close()

    def send(self, *messages):
        # Keep a batch in one application write, including all command boundaries.
        self.connection.sendall(b"".join(wire(message) for message in messages))

    def read_message(self, deadline):
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("QMP reply deadline expired")
            self.connection.settimeout(remaining)
            data = self.connection.recv(65536)
            if not data:
                raise RuntimeError("QMP disconnected before the expected reply")
            self.buffer.extend(data)
            if len(self.buffer) > MAX_LINE_BYTES:
                raise RuntimeError("QMP reply exceeds 1 MiB")
        line, _, remaining = self.buffer.partition(b"\n")
        self.buffer = bytearray(remaining)
        message = json.loads(line)
        if not isinstance(message, dict):
            raise RuntimeError(f"QMP reply is not an object: {describe(message)}")
        if "id" in message:
            ident = message["id"]
            if not isinstance(ident, str):
                raise RuntimeError(f"Unexpected QMP reply ID: {describe(message)}")
            if ident in self.seen_ids:
                raise RuntimeError(f"QMP replied twice to ID {ident[:100]!r}")
            self.seen_ids.add(ident)
        return message

    def next_response(self, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        for _ in range(128):
            message = self.read_message(deadline)
            if "event" not in message:
                if "return" not in message and "error" not in message:
                    raise RuntimeError(f"Unexpected QMP message: {describe(message)}")
                return message
        raise RuntimeError("QMP emitted too many events before its reply")

    def expect(self, expected, ordered=()):
        """Consume each expected ID once and check the selected IDs' order."""
        pending = dict(expected)
        received_order = []
        ordered = list(ordered)
        while pending:
            message = self.next_response()
            ident = message.get("id")
            if ident not in pending:
                raise RuntimeError(f"Unexpected QMP response: {describe(message)}")
            result_key = pending.pop(ident)
            if result_key not in message:
                raise RuntimeError(f"Expected {result_key} for {ident[:100]!r}: {describe(message)}")
            if ident in ordered:
                received_order.append(ident)
        if received_order != ordered:
            raise RuntimeError(f"In-band response order changed: {received_order!r}")

    def quiet(self, duration=QUIET_SECONDS):
        deadline = time.monotonic() + duration
        for _ in range(128):
            try:
                message = self.read_message(deadline)
            except TimeoutError:
                if self.buffer:
                    raise RuntimeError("QMP started an unexpected partial reply during a quiet interval")
                return
            if "event" not in message:
                raise RuntimeError(f"QMP replied during a quiet interval: {describe(message)}")
        raise RuntimeError("QMP emitted too many events during a quiet interval")

    def negotiate(self, ident, oob=False):
        self.send(command("qmp_capabilities", ident, {"enable": ["oob"]} if oob else {}))
        self.expect({ident: "return"}, [ident])


class Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


class BlockingConfigPipe:
    """An empty blkdebug config that reaches EOF only when release() runs."""

    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.LPVOID]
        self.kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                            wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
        self.kernel.ConnectNamedPipe.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped),
                                                  ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
        self.kernel.GetOverlappedResult.restype = wintypes.BOOL
        self.kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
        self.kernel.CancelIoEx.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.name = rf"\\.\pipe\qmp-input-{os.getpid()}-{uuid.uuid4().hex}"
        self.handle = None
        self.event = None
        self.pending = False
        self.connected = False
        self.overlapped = Overlapped()
        try:
            self.handle = self.kernel.CreateNamedPipeW(
                self.name, 0x00000002 | 0x40000000, 0, 1, 4096, 4096, 0, None)
            if self.handle == ctypes.c_void_p(-1).value:
                self.handle = None
                raise ctypes.WinError(ctypes.get_last_error())
            self.event = self.kernel.CreateEventW(None, True, False, None)
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
            self.overlapped.hEvent = self.event
            if self.kernel.ConnectNamedPipe(self.handle, ctypes.byref(self.overlapped)):
                self.connected = True
            else:
                error = ctypes.get_last_error()
                if error == 997:  # ERROR_IO_PENDING
                    self.pending = True
                elif error == 535:  # ERROR_PIPE_CONNECTED
                    self.connected = True
                else:
                    raise ctypes.WinError(error)
        except BaseException:
            self.release()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()

    def wait_connected(self):
        if self.connected:
            return
        result = self.kernel.WaitForSingleObject(self.event, int(TIMEOUT * 1000))
        if result == 258:  # WAIT_TIMEOUT
            raise TimeoutError("QEMU did not open the blocking config pipe")
        if result != 0:  # WAIT_OBJECT_0
            raise ctypes.WinError(ctypes.get_last_error())
        transferred = wintypes.DWORD()
        if not self.kernel.GetOverlappedResult(self.handle, ctypes.byref(self.overlapped),
                                              ctypes.byref(transferred), False):
            raise ctypes.WinError(ctypes.get_last_error())
        self.pending = False
        self.connected = True

    def release(self):
        cleanup_error = None
        if self.handle is not None:
            if self.pending:
                if not self.kernel.CancelIoEx(self.handle, ctypes.byref(self.overlapped)):
                    error = ctypes.get_last_error()
                    if error != 1168:  # ERROR_NOT_FOUND: operation already completed
                        cleanup_error = ctypes.WinError(error)
                result = self.kernel.WaitForSingleObject(self.event, int(TIMEOUT * 1000))
                if result != 0 and cleanup_error is None:
                    cleanup_error = RuntimeError("Timed out cancelling the config pipe connection")
                self.pending = False
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.event:
            self.kernel.CloseHandle(self.event)
            self.event = None
        if cleanup_error:
            raise cleanup_error

    def blocking_command(self, ident):
        return command("blockdev-add", ident,
                       {"driver": "blkdebug", "node-name": ident, "config": self.name,
                        "image": {"driver": "null-co", "read-zeroes": True}})


class QemuHarness:
    def __init__(self, process, port, control_port):
        self.process = process
        self.port = port
        self.control_port = control_port

    def connect(self, control=False):
        deadline = time.monotonic() + TIMEOUT
        while True:
            if self.process.poll() is not None:
                raise RuntimeError(f"QEMU exited before QMP connected: {self.process.returncode}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("QMP TCP connection deadline expired")
            try:
                port = self.control_port if control else self.port
                connection = socket.create_connection(("127.0.0.1", port),
                                                      timeout=min(0.25, remaining))
                break
            except (ConnectionRefusedError, TimeoutError):
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        qmp = QmpConnection(connection)
        try:
            greeting = qmp.read_message(time.monotonic() + TIMEOUT)
            if "QMP" not in greeting:
                raise RuntimeError(f"QMP greeting missing: {describe(greeting)}")
            return qmp
        except BaseException:
            qmp.close()
            raise


def test_batch_and_fragmentation(harness):
    with harness.connect() as qmp:
        qmp.negotiate("caps-basic")
        long_id = "large-" + "x" * 5000
        ids = ["batch-1", long_id, "batch-3"]
        messages = [command("query-name", ident) for ident in ids]
        payload_bytes = sum(len(wire(message)) for message in messages)
        if payload_bytes <= 4096 or len(wire(messages[1])) <= 4096:
            raise AssertionError("large command must span the raw input buffer")
        qmp.send(*messages)
        qmp.expect(dict.fromkeys(ids, "return"), ids)
        qmp.quiet()

        ident = "fragment-\u00b5-\U0001f41f"
        payload = wire(command("query-name", ident))
        split = payload.index("\U0001f41f".encode("utf-8")) + 1
        qmp.connection.sendall(payload[:split])
        qmp.quiet()
        qmp.connection.sendall(payload[split:split + 1])
        qmp.quiet()
        qmp.connection.sendall(payload[split + 1:-2])
        qmp.quiet()
        qmp.connection.sendall(payload[-2:])
        qmp.expect({ident: "return"}, [ident])

        qmp.connection.sendall(b"{]\n" + wire(command("query-name", "after-malformed")))
        malformed = qmp.next_response()
        if "error" not in malformed or "id" in malformed:
            raise RuntimeError(f"Malformed JSON did not return one anonymous error: {describe(malformed)}")
        qmp.expect({"after-malformed": "return"}, ["after-malformed"])
        qmp.quiet()
    return {"same_write_commands": 3, "payload_bytes": payload_bytes,
            "split_utf8": True, "malformed_recovered": True}


def test_partial_reconnect(harness):
    with harness.connect() as qmp:
        qmp.negotiate("caps-before-close")
        qmp.connection.sendall(b'{"execute":"query-name","id":"abandoned')
        qmp.quiet()
    with harness.connect() as qmp:
        qmp.negotiate("caps-after-close")
        qmp.send(command("query-name", "after-reconnect"))
        qmp.expect({"after-reconnect": "return"}, ["after-reconnect"])
        qmp.quiet()
    return {"partial_json_discarded": True, "reconnected": True}


def test_capability_batch(harness):
    with harness.connect() as qmp:
        qmp.send(command("qmp_capabilities", "caps-batched", {"enable": ["oob"]}),
                 command("query-yank", "oob-after-caps", oob=True))
        qmp.expect({"caps-batched": "return", "oob-after-caps": "return"},
                   ["caps-batched", "oob-after-caps"])
        qmp.quiet()
    return {"capability_reply_before_oob": True}


def test_no_oob_blocking_batch(harness):
    with harness.connect() as qmp:
        qmp.negotiate("caps-serial")
        with BlockingConfigPipe() as pipe:
            ids = ["serial-blocked", "serial-query", "serial-oob"]
            qmp.send(pipe.blocking_command(ids[0]),
                     command("query-yank", ids[2], oob=True),
                     command("query-name", ids[1]))
            pipe.wait_connected()
            qmp.quiet()
            pipe.release()
            qmp.expect({ids[0]: "return", ids[1]: "return", ids[2]: "error"},
                       [ids[0], ids[2], ids[1]])
            qmp.quiet()
    return {"blocked_batch_was_quiet": True, "in_band_order_preserved": True,
            "unnegotiated_oob_rejected": True}


def test_oob_full_queue(harness):
    with harness.connect() as qmp:
        qmp.negotiate("caps-full", oob=True)
        with BlockingConfigPipe() as pipe:
            blocked = "full-blocked"
            ids = [f"full-ib-{index}" for index in range(1, 9)]
            qmp.send(pipe.blocking_command(blocked))
            pipe.wait_connected()
            messages = [command("query-name", ident) for ident in ids[:7]]
            messages.extend([command("query-yank", "oob-before-full", oob=True),
                             command("query-name", ids[7]),
                             command("query-yank", "oob-after-full", oob=True)])
            qmp.send(*messages)
            qmp.expect({"oob-before-full": "return"}, ["oob-before-full"])
            qmp.quiet()
            pipe.release()
            expected = dict.fromkeys([blocked, *ids, "oob-after-full"], "return")
            qmp.expect(expected, [blocked, *ids])
            qmp.quiet()
    return {"queued_in_band_commands": 8, "oob_before_full_completed": True,
            "oob_after_full_waited_for_resume": True, "in_band_order_preserved": True}


def test_suspended_suffix_reconnect(harness):
    with harness.connect(control=True) as control:
        control.negotiate("caps-close-control")
        arguments = {"path": "/machine", "property": "dump-guest-core"}
        control.send(command("qom-get", "property-before-close", arguments))
        before = control.next_response()
        if before.get("id") != "property-before-close" or not isinstance(before.get("return"), bool):
            raise RuntimeError(f"Machine property query failed: {describe(before)}")
        original = before["return"]
        with harness.connect() as qmp, BlockingConfigPipe() as pipe:
            qmp.negotiate("caps-close-full", oob=True)
            qmp.send(pipe.blocking_command("close-blocked"))
            pipe.wait_connected()
            messages = [command("query-name", f"close-ib-{index}") for index in range(7)]
            messages.extend([
                command("query-yank", "close-before-full", oob=True),
                command("query-name", "close-ib-7"),
                command("qom-set", "discarded-raw-suffix", dict(arguments, value=not original)),
            ])
            qmp.send(*messages)
            qmp.expect({"close-before-full": "return"})
            qmp.quiet()
            # Reset the peer while an in-band command and raw suffix are pending.
            qmp.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                      struct.pack("HH", 1, 0))
            qmp.close()
            pipe.release()
            # A second monitor fences the old in-flight command before reconnect.
            control.send(command("query-name", "old-command-finished"))
            control.expect({"old-command-finished": "return"})
        with harness.connect() as qmp:
            qmp.negotiate("caps-after-suspended-close")
            qmp.send(command("query-name", "after-suspended-close"))
            qmp.expect({"after-suspended-close": "return"})
            qmp.quiet()
        control.send(command("qom-get", "property-after-close", arguments))
        after = control.next_response()
        if after.get("id") != "property-after-close" or after.get("return") is not original:
            raise RuntimeError(f"Suspended raw suffix executed after close: {describe(after)}")
        control.quiet()
    return {"closed_with_full_queue": True, "raw_suffix_discarded": True,
            "peer_reset_with_pending_input": True, "reconnected": True}


def run_suite(qemu, report):
    with socket.socket() as reservation, socket.socket() as control_reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        control_reservation.bind(("127.0.0.1", 0))
        control_port = control_reservation.getsockname()[1]
    launch = [str(qemu), "-machine", "none", "-nodefaults", "-display", "none",
              "-monitor", "none", "-serial", "none", "-S", "-accel", "tcg,tb-size=8",
              "-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off",
              "-qmp", f"tcp:127.0.0.1:{control_port},server=on,wait=off"]
    report["command"] = launch
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(launch, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        report["qemu_pid"] = process.pid
        harness = QemuHarness(process, port, control_port)
        try:
            for name, check in (("batch-fragmentation-malformed", test_batch_and_fragmentation),
                                ("partial-close-reconnect", test_partial_reconnect),
                                ("capabilities-and-oob-batch", test_capability_batch),
                                ("no-oob-blocking-batch", test_no_oob_blocking_batch),
                                ("oob-full-queue-resume", test_oob_full_queue),
                                ("suspended-raw-suffix-close", test_suspended_suffix_reconnect)):
                case = {"name": name, "passed": False}
                report["cases"].append(case)
                try:
                    case.update(check(harness))
                    case["passed"] = True
                except (Exception, KeyboardInterrupt) as error:
                    case.update(error=str(error), error_type=type(error).__name__)
                    raise
                print(json.dumps(case), flush=True)
            with harness.connect() as qmp:
                qmp.negotiate("caps-shutdown")
                qmp.send(command("quit", "quit"))
                qmp.expect({"quit": "return"}, ["quit"])
            process.wait(timeout=TIMEOUT)
            if process.returncode:
                raise RuntimeError(f"QEMU exited with status {process.returncode}")
        finally:
            try:
                if process.poll() is None:
                    report["terminated_after_failure"] = True
                    process.terminate()
                    try:
                        process.wait(timeout=TIMEOUT)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=TIMEOUT)
            finally:
                report["returncode"] = process.returncode
                report["reaped"] = process.returncode is not None
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - TAIL_BYTES))
                report["stderr_tail"] = stderr.read().decode("utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True,
                        help="explicit native Windows QEMU executable to test")
    parser.add_argument("--report", type=Path, required=True,
                        help="new JSON report path; existing reports are never replaced")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("this smoke test requires native Windows")
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
            run_suite(qemu, report)
            report["status"] = "passed"
            code = 0
        except (Exception, KeyboardInterrupt) as error:
            report.update(error=str(error), error_type=type(error).__name__)
            code = 130 if isinstance(error, KeyboardInterrupt) else 1
        json.dump(report, output, indent=2)
        output.write("\n")
    print(f"Windows QMP input: {report['status']} ({report_path})", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
