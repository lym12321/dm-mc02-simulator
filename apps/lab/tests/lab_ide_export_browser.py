#!/usr/bin/env python3
"""Exercise IDE export against the real backend, with a deterministic save picker."""
import argparse
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright


PICKER = """(() => {
  window.__pickerCalls = [];
  window.__savedFiles = [];
  window.__pickerMode = 'save';
  window.showSaveFilePicker = async (options) => {
    window.__pickerCalls.push(options);
    if (window.__pickerMode === 'cancel') throw new DOMException('Cancelled', 'AbortError');
    let contents;
    return {name: options.suggestedName, createWritable: async () => ({
      write: async (text) => {
        if (window.__pickerMode === 'write-error') throw new Error('Disk is full');
        contents = text;
      },
      close: async () => window.__savedFiles.push({name: options.suggestedName, contents}),
      abort: async () => { window.__abortedWrite = true; },
    })};
  };
})();"""


def check_exports(page, firmware):
    page.wait_for_function("!document.querySelector('#upload-button').disabled")
    page.locator("#settings-button").click()
    page.locator("#settings-dialog mdui-collapse-item").first.locator(".header").click()
    page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#project-gdb').checked=true; document.querySelector('#project-gdb-port').value='4321';}", str(firmware))
    for index, format in enumerate(("vscode", "clion", "gdb"), 1):
        page.evaluate("format => {document.querySelector('#ide-format').value=format;}", format)
        page.locator("#ide-download").click()
        page.wait_for_function("count => window.__savedFiles.length === count", arg=index, timeout=5000)
        assert "已保存" in page.locator("#ide-export-status").inner_text()
    saved = page.evaluate("window.__savedFiles")
    assert [item["name"] for item in saved] == ["launch.json", "DM-MC02.run.xml", "attach.gdb"]
    vscode = json.loads(saved[0]["contents"])["configurations"][0]
    assert vscode["type"] == "cortex-debug" and vscode["servertype"] == "external"
    assert vscode["gdbTarget"] == "127.0.0.1:4321"
    assert vscode["executable"] == str(firmware)
    configuration = ET.fromstring(saved[1]["contents"]).find("configuration")
    assert configuration is not None and configuration.attrib["name"] == "DM-MC02"
    assert configuration.attrib["type"] == "CLion_Remote"
    assert configuration.attrib["factoryName"] == "Remote Debug"
    assert configuration.attrib["version"] == "1"
    assert configuration.attrib["remoteCommand"] == "127.0.0.1:4321"
    assert configuration.attrib["symbolFile"] == str(firmware).replace("\\", "/")
    assert configuration.find("debugger").attrib == {"kind": "GDB", "isBundled": "true"}
    assert "target remote 127.0.0.1:4321" in saved[2]["contents"]
    symbols = str(firmware.with_name("symbols & debug.elf"))
    page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#ide-format').value='clion';}", symbols)
    page.locator("#ide-download").click()
    page.wait_for_function("window.__savedFiles.length === 4")
    escaped = ET.fromstring(page.evaluate("window.__savedFiles[3].contents")).find("configuration")
    assert escaped.attrib["symbolFile"] == symbols.replace("\\", "/")
    page.evaluate("window.__pickerMode='cancel'")
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-export-status').textContent.includes('已取消')")
    assert page.locator("#ide-error").is_hidden()
    assert page.evaluate("window.__savedFiles.length") == 4
    page.evaluate("window.__pickerMode='write-error'")
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('Disk is full')")
    assert page.evaluate("window.__abortedWrite")
    assert page.evaluate("window.__savedFiles.length") == 4
    calls = page.evaluate("window.__pickerCalls.length")
    page.evaluate("document.querySelector('#project-firmware').value=''")
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('本地 ELF 路径')")
    assert page.evaluate("window.__pickerCalls.length") == calls
    page.evaluate("firmware => {document.querySelector('#project-firmware').value=firmware; document.querySelector('#project-gdb').checked=false;}", str(firmware))
    page.locator("#ide-download").click()
    page.wait_for_function("document.querySelector('#ide-error').textContent.includes('启用 GDB')")
    assert page.evaluate("window.__pickerCalls.length") == calls
    return {"exports": saved, "cancelled_without_write": True, "write_failure_reported": True,
            "missing_local_symbols_reported": True, "gdb_requirement_reported": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--browser", default="msedge")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed"}
    with (args.output / "backend.log").open("wb") as log:
        process = subprocess.Popen([str(args.backend.resolve()), "--port", "0", "--assets-dir", str(Path(__file__).resolve().parents[1] / "src")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        try:
            ready = json.loads(process.stdout.readline())
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.browser, headless=True)
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script(PICKER)
                page.goto(ready["url"])
                report.update(check_exports(page, args.elf.resolve()))
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
