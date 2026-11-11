#!/usr/bin/env python3
"""Exercise the built Windows app through its actual WebView2 debug endpoint.

Debugging is enabled only in the test child's environment. Close each native
window with its titlebar when the test prints its ready-to-close message; the
test then verifies exit and child cleanup. Dependencies: tests/requirements.txt.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import psutil
from playwright.sync_api import Error as PlaywrightError, sync_playwright


def descendants(pid):
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def identity(process):
    return (process.pid, process.create_time())


def alive(item):
    try:
        return psutil.Process(item[0]).create_time() == item[1]
    except psutil.NoSuchProcess:
        return False


def wait_until(predicate, label, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError(f"Timed out: {label}")


def close_window(page, process, output, case):
    # CDP window.close() only destroys WebView2's child HWND. Have the tester
    # close the actual native window so this exercises Tauri's normal exit path.
    (output / f"{case}-ready-to-close.json").write_text(
        json.dumps({"pid": process.pid, "title": page.title(), "case": case}), encoding="utf-8"
    )
    print(f"Ready to close native {case} window (PID {process.pid}).", flush=True)
    assert process.wait(timeout=180) == 0


def launch(exe, output, case):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    system_directory = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetWindowsDirectoryW(system_directory, len(system_directory)):
        raise ctypes.WinError()
    system_root = system_directory.value
    env["SystemRoot"] = system_root
    env["WINDIR"] = system_root
    env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root])
    env.pop("QEMU_SYSTEM_ARM", None)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={port}"
    env["WEBVIEW2_USER_DATA_FOLDER"] = str(output / f"profile-{case}")
    log = (output / f"{case}.log").open("wb")
    process = subprocess.Popen([str(exe)], cwd=output, env=env, stdout=log, stderr=log)
    endpoint = f"http://127.0.0.1:{port}"

    def endpoint_ready():
        if process.poll() is not None:
            raise AssertionError(f"Desktop exited early: {process.returncode}")
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=.3) as response:
                return response.status == 200
        except OSError:
            return False

    try:
        wait_until(endpoint_ready, "WebView2 debug endpoint")
    except BaseException:
        for child in descendants(process.pid):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        log.close()
        raise
    return process, log, endpoint


def run_case(exe, output, case, elf=None):
    process, log, endpoint = launch(exe, output, case)
    owned = []
    report = {"case": case, "desktop_pid": process.pid, "minimal_path": True}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint)
            context = browser.contexts[0]
            errors = []
            if case == "early-exit":
                def failure_page():
                    for page in context.pages:
                        try:
                            if "未能继续运行" in page.locator("h1").inner_text(timeout=200):
                                return page
                        except PlaywrightError:
                            pass
                    return None
                page = wait_until(failure_page, "persistent local failure page")
                page.wait_for_timeout(800)
                assert "仿真后端已退出" in page.locator("body").inner_text()
                assert page.url.startswith(("http://tauri.localhost", "tauri://localhost")), page.url
                report["diagnostic"] = page.locator("body").inner_text()
                page.screenshot(path=str(output / f"{case}.png"))
                close_window(page, process, output, case)
            else:
                page = wait_until(lambda: next((p for p in context.pages if re.match(r"http://127\.0\.0\.1:\d+", p.url)), None), "Lab page")
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.wait_for_function("!document.querySelector('#upload-button').disabled")
                report["url"] = page.url
                report["initial_state"] = page.request.get(page.url + "api/state").json()["status"]
                assert report["initial_state"] == "idle"
                assert not any(p.name() == "qemu-system-arm.exe" for p in descendants(process.pid))
                assert page.evaluate("typeof window.__TAURI__") == "undefined"
                page.locator("#firmware-input").set_input_files(str(elf))
                page.wait_for_function("name => document.querySelector('#firmware-name').textContent.includes(name) && !document.querySelector('#primary-button').disabled", arg=elf.name)
                page.locator("#primary-button").click()
                page.wait_for_function("document.querySelector('#primary-button').textContent==='暂停'")
                report["firmware"] = page.locator("#firmware-name").inner_text()
                assert elf.name in report["firmware"]
                owned = [identity(p) for p in descendants(process.pid) if p.name() in ("sim-lab-backend.exe", "qemu-system-arm.exe")]
                assert len(owned) == 2, owned
                page.screenshot(path=str(output / "running.png"))
                page.locator("#primary-button").click()
                page.wait_for_function("document.querySelector('#session-status').textContent==='已暂停'")
                page.locator("#primary-button").click()
                page.wait_for_function("document.querySelector('#primary-button').textContent==='暂停'")
                page.locator("#stop-button").click()
                page.wait_for_function("document.querySelector('#session-status').textContent==='已停止'")
                report["pause_resume_stop"] = True
                # Exit with another active session, rather than merely an idle backend.
                page.locator("#primary-button").click()
                page.wait_for_function("document.querySelector('#primary-button').textContent==='暂停'")
                owned += [identity(p) for p in descendants(process.pid) if p.name() == "qemu-system-arm.exe"]
                close_window(page, process, output, case)
                report["closed_with_active_session"] = True
            browser.close()
            assert not errors, errors
            report["page_errors"] = errors
        wait_until(lambda: not any(alive(item) for item in owned), "owned backend/QEMU cleanup", timeout=5)
        report["exit_code"] = process.returncode
        report["owned_processes_cleaned"] = True
        report["status"] = "passed"
        return report
    finally:
        # Only processes created by this test are eligible for failure cleanup.
        if process.poll() is None:
            for child in descendants(process.pid):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
            process.wait(timeout=5)
        log.close()


def early_exit_fixture(exe, output):
    fixture = Path(tempfile.mkdtemp(prefix="early-exit-app-", dir=output))
    shutil.copytree(exe.parent, fixture, dirs_exist_ok=True)
    source = output / "early-exit-backend.rs"
    source.write_text('''use std::io::{self, Write};
fn main() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    println!("{{\\"event\\":\\"lab-ready\\",\\"protocol\\":\\"dm-mc02-lab/v1\\",\\"url\\":\\"http://127.0.0.1:{}\\",\\"pid\\":{}}}", listener.local_addr().unwrap().port(), std::process::id());
    io::stdout().flush().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(30));
    std::process::exit(7);
}
''', encoding="utf-8")
    subprocess.run(["rustc", str(source), "--crate-name", "early_exit_backend", "-o", str(fixture / "runtime/sim-lab-backend.exe")], check=True)
    return fixture / exe.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-early-exit", action="store_true", help="compile a tiny Rust backend fixture")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    for case in ("normal", "early-exit"):
        (args.output / f"{case}-ready-to-close.json").unlink(missing_ok=True)
    results = [run_case(args.app.resolve(), args.output, "normal", args.elf.resolve())]
    if args.check_early_exit:
        fixture = early_exit_fixture(args.app.resolve(), args.output)
        results.append(run_case(fixture, args.output, "early-exit"))
    (args.output / "report.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
