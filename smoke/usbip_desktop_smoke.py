#!/usr/bin/env python3
"""Visible MDUI USB actions against actual firmware, Linux vhci and host tools."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from smoke import dm_mc02_desktop_smoke as desktop
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backend', 'qemu', 'elf', 'trobot', 'dependencies', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--usbip-port', type=int, default=33241)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'failed', 'server': {}, 'inputs': {
        str(p): desktop.sha(p) for p in (args.backend, args.qemu, args.elf)}}
    before = Path('/sys/devices/platform/vhci_hcd.0/status').read_text()
    server = desktop.Server(time.monotonic() + 60, report['server'], args.backend.resolve())
    playwright = browser = page = None
    primary = None
    try:
        server.start(args.qemu.resolve(), ['--elf', str(args.elf.resolve()),
                     '--usbip', f'127.0.0.1:{args.usbip_port}'])
        url = server.ready()
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        desktop.wait_for(page, server, lambda: desktop.button(page, 'primary-button').is_enabled(), 'start control')
        desktop.button(page, 'settings-button').click()
        page.wait_for_function("document.querySelector('#settings-dialog').open")
        desktop.choose(page, 'project-startup', 'user')
        desktop.fill(page, 'project-user-hold', '1000')
        desktop.button(page, 'settings-apply').click()
        page.wait_for_function("!document.querySelector('#settings-dialog').open")
        desktop.button(page, 'primary-button').click()
        desktop.wait_for(page, server, lambda: desktop.status(page) == 'running' and
                     int(desktop.board(page)['timestamp_ns']) > 200_000_000, 'debug boot')
        desktop.peripheral(page, 'usb')
        desktop.wait_for(page, server, lambda: desktop.button(page, 'usb-attach').is_enabled(), 'USB available')
        desktop.button(page, 'usb-attach').click()
        desktop.wait_for(page, server, lambda: desktop.button(page, 'usb-detach').is_enabled(), 'OS USB attachment')
        assert '已连接' in page.locator('#usb-status').text_content()
        consumer = subprocess.run(['sudo', '-n', str(Path(sys.executable).absolute()),
            str(ROOT / 'smoke/trobot_usb_os_consumer.py'), '--trobot', str(args.trobot),
            '--dependencies', str(args.dependencies)], capture_output=True, text=True, timeout=15)
        report['os_consumer'] = {'exit': consumer.returncode, 'stdout': consumer.stdout, 'stderr': consumer.stderr}
        assert consumer.returncode == 0, report['os_consumer']
        page.locator('#usb-status').scroll_into_view_if_needed()
        page.screenshot(path=str(args.output / 'usb-attached.png'), full_page=True)
        report['screenshot'] = str(args.output / 'usb-attached.png')
        desktop.button(page, 'usb-detach').click()
        desktop.wait_for(page, server, lambda: desktop.button(page, 'usb-attach').is_enabled(), 'USB detached')
        desktop.button(page, 'usb-attach').click()
        desktop.wait_for(page, server, lambda: desktop.button(page, 'usb-detach').is_enabled(), 'USB reattached')
        desktop.close_peripheral(page)
        desktop.button(page, 'stop-button').click()
        desktop.wait_for(page, server, lambda: desktop.status(page) == 'stopped', 'stop while attached')
        desktop.peripheral(page, 'usb')
        assert desktop.button(page, 'usb-attach').is_disabled()
        assert desktop.button(page, 'usb-detach').is_disabled()
        assert Path('/sys/devices/platform/vhci_hcd.0/status').read_text() == before, 'vhci port survived Stop'
        assert not errors, errors
        report['page_errors'] = errors
        report['actions'] = ['attach', 'detach', 'reattach', 'stop_while_attached']
    except BaseException as error:
        primary = error
        report['error'] = str(error)
        if page is not None:
            report['last_state'] = page.request.get(url + '/api/state').json()
            page.screenshot(path=str(args.output / 'failed.png'), full_page=True)
        raise
    finally:
        try:
            desktop.cleanup(primary, [('Chromium', lambda: browser and browser.close()),
                ('Playwright', lambda: playwright and playwright.stop()), ('Web server', server.close)])
            assert Path('/sys/devices/platform/vhci_hcd.0/status').read_text() == before, 'vhci port leaked'
            if primary is None:
                report['status'] = 'passed'
        finally:
            (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
