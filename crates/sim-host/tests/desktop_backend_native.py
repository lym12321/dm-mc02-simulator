#!/usr/bin/env python3
"""Cross-platform acceptance for the desktop backend process contract.

Run with a desktop-backend binary, native QEMU, and a small board ELF fixture.
aiohttp and psutil are independent acceptance tools, not product dependencies.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading

from aiohttp import ClientSession
import psutil


TIMEOUT = 15
PROTOCOL = "dm-mc02-lab/v1"
ASSETS = (
    "index.html", "app.js", "uart-console.js", "style.css",
    "vendor/mdui.js", "vendor/mdui.css", "vendor/xterm.js",
    "vendor/xterm.css", "vendor/addon-fit.js",
)


def stage_assets(root, target):
    for name in ASSETS:
        source = root / "apps/lab/src" / name
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    (target / "index.html").write_text("<html>desktop-snapshot</html>", encoding="utf-8")
    (target / "private.txt").write_text("not-an-asset", encoding="utf-8")


def start(argv, **kwargs):
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        **kwargs,
    )
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        ready = lines.get(timeout=TIMEOUT)
    except queue.Empty:
        process.kill()
        raise AssertionError("backend did not announce readiness")
    if not ready:
        raise AssertionError(f"backend exited before readiness: {process.stderr.read().decode(errors='replace')}")
    return process, ready


def identity(process):
    return process.pid, process.create_time()


def alive(value):
    try:
        return psutil.Process(value[0]).create_time() == value[1]
    except psutil.NoSuchProcess:
        return False


async def desktop_case(args, assets, shutdown):
    process, line = start([
        str(args.backend), "--assets-dir", str(assets),
        "--qemu", str(args.qemu), "--elf", str(args.elf), "--port", "0",
        "--timeout", "60", "--no-gdb", "--no-usbip",
    ], stdin=subprocess.PIPE)
    children = []
    try:
        ready = json.loads(line)
        assert set(ready) == {"event", "protocol", "url", "pid"}, ready
        assert ready["event"] == "lab-ready" and ready["protocol"] == PROTOCOL
        assert ready["pid"] == process.pid
        url = ready["url"]
        assert url.startswith("http://127.0.0.1:") and int(url.rsplit(":", 1)[1]) > 0
        async with ClientSession() as client:
            info = await (await client.get(url + "/api/info")).json()
            assert info["protocol"] == PROTOCOL and info["backend_version"]
            assert {"external-assets", "stdin-shutdown"} <= set(info["capabilities"])
            assert (await (await client.get(url + "/api/state")).json())["status"] == "idle"
            assert not psutil.Process(process.pid).children(recursive=True)
            # Resources remain a startup snapshot and the allowlist stays exact.
            (assets / "index.html").unlink()
            response = await client.get(url)
            assert response.status == 200 and "desktop-snapshot" in await response.text()
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert (await client.get(url + "/private.txt")).status == 404
            for headers in ({"Origin": "tauri://localhost"}, {"Host": "foreign.example"}):
                assert (await client.get(url + "/api/info", headers=headers)).status == 403
            ide_project = dict((await (await client.get(url + "/api/project")).json())["project"],
                               firmware=str(args.elf), gdb_port=4321)
            save_request = {"format": "clion", "project": ide_project}
            for headers in ({}, {"Origin": "null"}, {"Origin": "https://foreign.example"}):
                assert (await client.post(url + "/api/ide/save", json=save_request, headers=headers)).status == 403
            # Unsupported format, caller paths, and caller content are refused
            # before any native dialog can open.
            for payload in (dict(save_request, format="other"),
                            dict(save_request, path="arbitrary.xml"),
                            dict(save_request, text="arbitrary content")):
                assert (await client.post(url + "/api/ide/save", json=payload,
                                          headers={"Origin": url})).status == 422
            assert (await client.post(url + "/api/ide/save", json={"format": "clion", "project": dict(ide_project, firmware=None)},
                                      headers={"Origin": url})).status == 400
            preview = await client.post(url + "/api/ide", json=ide_project)
            assert preview.status == 200 and (await preview.json())["clion"]["target"] == "127.0.0.1:4321"
            async with client.ws_connect(url + "/api/ws", origin=url) as ws:
                async def command(identifier, action, generation):
                    await ws.send_json({"id": identifier, "session_id": generation, "action": action, "args": {}})
                    async with asyncio.timeout(TIMEOUT):
                        while True:
                            value = await ws.receive_json()
                            if value.get("type") == "command_result" and value.get("id") == identifier:
                                assert value["ok"], value
                                return value
                loaded = await command("load", "load", None)
                await command("run", "run", loaded["session_id"])
                children = [identity(child) for child in psutil.Process(process.pid).children(recursive=True)]
                assert children, "load/run did not create QEMU"
                process.stdin.write(b"ignored-parent-line\n")
                process.stdin.flush()
                assert process.poll() is None
                if shutdown == "eof":
                    process.stdin.close()
                else:
                    process.stdin.write(b"shutdown\r\n" if shutdown == "crlf" else b"shutdown\n")
                    process.stdin.flush()
                code = await asyncio.to_thread(process.wait, TIMEOUT)
                assert code == 0, process.stderr.read().decode(errors="replace")
        assert not any(alive(child) for child in children), "QEMU survived desktop shutdown"
        assert process.stdout.read() == b"", "stdout contained more than one ready line"
        return {"shutdown": shutdown, "ready": ready, "children_cleaned": len(children)}
    finally:
        if process.poll() is None:
            if not process.stdin.closed:
                process.stdin.close()
            try:
                await asyncio.to_thread(process.wait, TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, TIMEOUT)
        for stream in (process.stdout, process.stderr, process.stdin):
            if not stream.closed:
                stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    args = parser.parse_args()
    args.backend, args.qemu, args.elf = args.backend.resolve(), args.qemu.resolve(), args.elf.resolve()
    root = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix="desktop-backend-test.") as directory:
        assets = Path(directory) / "assets"
        assets.mkdir()
        # Missing required files must fail before any ready event is emitted.
        failed = subprocess.run([
            str(args.backend), "--assets-dir", str(assets), "--qemu", str(args.qemu),
        ], input=b"", capture_output=True, timeout=TIMEOUT)
        assert failed.returncode != 0 and not failed.stdout and b"index.html" in failed.stderr
        report = []
        for shutdown in ("eof", "command", "crlf"):
            stage_assets(root, assets)
            report.append(asyncio.run(desktop_case(args, assets, shutdown)))
        print(json.dumps({"desktop": report}, indent=2))


if __name__ == "__main__":
    main()
