#!/usr/bin/env python3
"""Verify IDE export through the app's native Save As dialogs.

For each checkpoint, save to the exact path printed by this test; cancel the
third dialog. Close only this test's window when the final checkpoint appears.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import psutil
from playwright.sync_api import sync_playwright

from desktop_smoke import alive, close_window, descendants, identity, launch, wait_until


def checkpoint(output, process, case, path=None):
    backend_pids = []
    for child in descendants(process.pid):
        try:
            if child.name() == "sim-lab-backend.exe":
                backend_pids.append(child.pid)
        except psutil.NoSuchProcess:
            pass
    assert len(backend_pids) == 1, backend_pids
    value = {"pid": process.pid, "backend_pid": backend_pids[0], "case": case}
    if path is not None:
        value["save_path"] = str(path)
    (output / f"{case}-dialog.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    print(f"Ready for native {case} dialog (desktop PID {process.pid}, backend PID {backend_pids[0]}); destination: {path or 'cancel'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-close-during-save", action="store_true", help="open one more Save As dialog, then close the desktop window")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    firmware = args.elf.resolve()
    process, log, endpoint = launch(args.app.resolve(), args.output, "ide-export")
    report = {"status": "failed", "desktop_pid": process.pid}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            context = browser.contexts[0]
            page = wait_until(lambda: next((page for page in context.pages if re.match(r"http://127\.0\.0\.1:\d+", page.url)), None), "Lab page")
            errors = []
            save_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: save_requests.append(request.post_data_json) if request.url.endswith("/api/ide/save") else None)
            page.wait_for_function("!document.querySelector('#upload-button').disabled")
            assert page.locator("#uart-newline").evaluate("element=>element.value") == "crlf"
            assert page.evaluate("typeof window.__TAURI__") == "undefined"
            page.locator("#firmware-input").set_input_files(str(firmware))
            page.wait_for_function("name => document.querySelector('#firmware-name').textContent.includes(name)", arg=firmware.name)
            page.locator("#settings-button").click()
            page.locator("#settings-dialog mdui-collapse-item").first.locator(".header").click()
            page.locator("#ide-download").click()
            page.wait_for_function("document.querySelector('#ide-error').textContent.includes('本地 ELF 路径')")
            page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#project-gdb').checked=true; document.querySelector('#project-gdb-port').value='4321';}", str(firmware))
            for format, filename in (("vscode", "launch.json"), ("clion", "DM-MC02.run.xml")):
                destination = args.output / filename
                assert not destination.exists(), f"Use a fresh output directory: {destination}"
                page.evaluate("format => {document.querySelector('#ide-format').value=format;}", format)
                page.locator("#ide-download").click()
                checkpoint(args.output, process, format, destination)
                page.wait_for_function("document.querySelector('#ide-export-status').textContent.startsWith('已保存') && !document.querySelector('#ide-download').disabled", timeout=180000)
                assert destination.is_file() and destination.stat().st_size > 0
            vscode = json.loads((args.output / "launch.json").read_text(encoding="utf-8"))["configurations"][0]
            assert vscode["gdbTarget"] == "127.0.0.1:4321" and vscode["executable"] == str(firmware)
            assert vscode["servertype"] == "external" and vscode["type"] == "cortex-debug"
            xml = ET.fromstring((args.output / "DM-MC02.run.xml").read_text(encoding="utf-8"))
            configuration = xml.find("configuration")
            assert configuration is not None
            assert configuration.attrib["type"] == "CLion_Remote" and configuration.attrib["factoryName"] == "Remote Debug"
            assert configuration.attrib["remoteCommand"] == "127.0.0.1:4321"
            assert configuration.attrib["symbolFile"] == str(firmware).replace("\\", "/")
            assert configuration.find("debugger").attrib == {"kind": "GDB", "isBundled": "true"}
            before = {name: (args.output / name).read_bytes() for name in ("launch.json", "DM-MC02.run.xml")}
            page.locator("#ide-download").click()
            checkpoint(args.output, process, "cancel")
            page.wait_for_function("document.querySelector('#ide-export-status').textContent.includes('已取消')", timeout=180000)
            assert page.locator("#ide-error").is_hidden()
            assert all((args.output / name).read_bytes() == contents for name, contents in before.items())
            page.screenshot(path=str(args.output / "saved-and-cancelled.png"))
            assert len(save_requests) == 3 and [request["format"] for request in save_requests] == ["vscode", "clion", "clion"]
            assert all(set(request) == {"format", "project"} for request in save_requests)
            report.update({"files": list(before), "gdb_target": "127.0.0.1:4321", "firmware": str(firmware), "uart_default_ending": "crlf", "cancel_preserved_files": True, "no_native_ipc_exposed": True, "save_endpoint": "/api/ide/save", "save_requests": save_requests})
            owned = [identity(child) for child in descendants(process.pid) if child.name() in ("sim-lab-backend.exe", "qemu-system-arm.exe")]
            if args.check_close_during_save:
                page.locator("#ide-download").click()
                checkpoint(args.output, process, "close-during-save")
                report["closed_with_open_save_dialog"] = True
                close_window(page, process, args.output, "ide-export-active-save")
            else:
                close_window(page, process, args.output, "ide-export")
            browser.close()
        wait_until(lambda: not any(alive(item) for item in owned), "owned backend cleanup", timeout=5)
        assert not errors, errors
        report.update({"status": "passed", "exit_code": process.returncode, "owned_processes_cleaned": True, "page_errors": errors})
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        if process.poll() is None:
            for child in descendants(process.pid):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            process.wait(timeout=5)
        log.close()
        (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
