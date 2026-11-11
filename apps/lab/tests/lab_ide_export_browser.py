#!/usr/bin/env python3
"""Verify the frontend's restricted IDE-save request and response handling."""
import argparse
import json
from pathlib import Path
import subprocess

from playwright.sync_api import sync_playwright


FILENAMES = {"vscode": "launch.json", "clion": "DM-MC02.run.xml", "gdb": "attach.gdb"}


def check_exports(page, firmware, requests, reply_mode, output):
    page.wait_for_function("!document.querySelector('#upload-button').disabled")
    page.locator("#settings-button").click()
    page.locator("#settings-dialog mdui-collapse-item").first.locator(".header").click()
    page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#project-gdb').checked=true; document.querySelector('#project-gdb-port').value='4321';}", str(firmware))
    for format, filename in FILENAMES.items():
        page.evaluate("format => {document.querySelector('#ide-format').value=format;}", format)
        page.locator("#ide-download").click()
        page.wait_for_function("name => document.querySelector('#ide-export-status').textContent.startsWith('已保存 ' + name)", arg=filename, timeout=5000)
        assert page.locator("#ide-export-status").get_attribute("title") == str(output / filename)
        request = requests[-1]
        assert set(request) == {"format", "project"}
        assert request["format"] == format
        assert request["project"]["schema"] == "sim/lab-project/v1"
        assert request["project"]["firmware"] == str(firmware)
        assert request["project"]["gdb_port"] == 4321
    assert [request["format"] for request in requests] == ["vscode", "clion", "gdb"]
    reply_mode["status"] = "cancelled"
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-export-status').textContent.includes('已取消')")
    assert page.locator("#ide-error").is_hidden()
    assert not page.locator("#ide-download").is_disabled()
    reply_mode["status"] = "error"
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('Disk is full')")
    assert not page.locator("#ide-download").is_disabled()
    assert page.locator("#ide-export-status").inner_text() == ""
    calls = len(requests)
    page.evaluate("document.querySelector('#project-firmware').value=''")
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('本地 ELF 路径')")
    assert len(requests) == calls
    page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#project-gdb').checked=false;}", str(firmware))
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('启用 GDB')")
    assert len(requests) == calls
    return {"save_requests": requests, "saved_response_reported": True, "cancel_reported": True,
            "write_failure_reported": True, "missing_local_symbols_blocked": True,
            "missing_gdb_blocked": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--browser", default="msedge")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "scope": "frontend request/response contract; native dialog and file contents are verified separately"}
    with (args.output / "backend.log").open("wb") as log:
        process = subprocess.Popen([str(args.backend.resolve()), "--port", "0", "--assets-dir", str(Path(__file__).resolve().parents[1] / "src")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        try:
            ready = json.loads(process.stdout.readline())
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.browser, headless=True)
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                requests = []
                reply_mode = {"status": "saved"}
                page.on("pageerror", lambda error: errors.append(str(error)))

                def save_route(route, request):
                    assert request.method == "POST"
                    assert request.headers["content-type"] == "application/json"
                    assert request.headers["origin"] == ready["url"].rstrip("/")
                    value = request.post_data_json
                    requests.append(value)
                    mode = reply_mode["status"]
                    if mode == "error":
                        route.fulfill(status=500, json={"error": "Disk is full"})
                    elif mode == "cancelled":
                        route.fulfill(json={"status": "cancelled"})
                    else:
                        route.fulfill(json={"status": "saved", "path": str(args.output / FILENAMES[value["format"]])})

                page.route("**/api/ide/save", save_route)
                page.goto(ready["url"])
                report.update(check_exports(page, args.elf.resolve(), requests, reply_mode, args.output))
                page.screenshot(path=str(args.output / "settings.png"))
                assert not errors, errors
                report["page_errors"] = errors
                browser.close()
            report["status"] = "passed"
        except BaseException as error:
            report["error"] = repr(error)
            raise
        finally:
            if process.poll() is None:
                process.stdin.write(b"shutdown\n")
                process.stdin.flush()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            report["backend_exit"] = process.returncode
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
