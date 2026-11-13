#!/usr/bin/env python3
"""Known public snapshots → real offline CAD/WebGL and SVG board presentation."""
import argparse
import json
import mimetypes
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview", type=Path, help="Also open the self-contained preview via file://, offline")
    parser.add_argument("--browser-channel", choices=["chromium", "msedge", "chrome"], default="chromium")
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    report = {"status": "failed", "errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--enable-unsafe-swiftshader"],
            **({"channel": args.browser_channel} if args.browser_channel != "chromium" else {}))
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            page.on("pageerror", lambda error: report["errors"].append(str(error)))

            def asset(route):
                name = route.request.url.removeprefix("http://board.test/") or "index.html"
                path = ROOT / "src" / name
                body = path.read_bytes()
                if name == "index.html":
                    body = body.replace(b'<script src="/app.js" defer></script>', b"")
                route.fulfill(body=body, content_type=mimetypes.guess_type(name)[0] or "application/octet-stream")

            page.route("http://board.test/**", asset)
            page.goto("http://board.test/")
            page.evaluate("""async () => {
                window.actions=[];
                window.snapshot={status:'idle',board:null,pins:[]};
                window.channels={uart:['uart1','uart2','uart3','uart5','uart7','uart10'],can:['can1','can2','can3']};
                window.view=new BoardView((component,intent)=>{
                    actions.push({endpoint:component.endpoint,intent});
                    view.update(snapshot,true,channels);
                });
                await view.ready;
                view.update(snapshot,true,channels);
            }""")
            assert page.locator("#board-mode").evaluate("e=>e.value") == "3d"
            assert page.locator("#board-model-error").is_hidden()
            page.locator('#board-interaction mdui-segmented-button[value="camera"]').click()
            assert page.evaluate("view.model.parts.size") == 293  # Original STEP assembly count.
            assert page.locator("#board-model").get_attribute("aria-label") == "三维板卡，LED 无数据"
            page.screenshot(path=str(args.output / "board-3d.png"))
            # Native CAD geometry is pickable, including a component absent from the old SVG.
            page.locator("#board-top").click()
            point = page.evaluate("view.model.project('U14')")
            rect = page.locator("#board-model").bounding_box()
            page.mouse.click(rect["x"] + point["x"], rect["y"] + point["y"], button="right")
            assert page.evaluate("actions.length") == 0, "pan button selected a component"
            page.mouse.click(rect["x"] + point["x"], rect["y"] + point["y"])
            assert page.locator("#board-detail-title").inner_text() == "STM32H723VGT6"
            before = page.evaluate("view.model.project('SW1')")
            actions = page.evaluate("actions.length")
            page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
            page.mouse.down()
            page.mouse.move(rect["x"] + rect["width"] / 2 + 95, rect["y"] + rect["height"] / 2 + 45, steps=6)
            page.mouse.up()
            assert page.evaluate("view.model.project('SW1')") != before
            assert page.evaluate("actions.length") == actions, "camera movement issued a board action"
            page.locator("#board-bottom").click()
            page.wait_for_function("document.querySelector('#key-button').hidden")
            page.screenshot(path=str(args.output / "board-3d-bottom.png"))
            page.locator("#board-fit").click()
            # Search selects an interface without activating it.
            page.locator("#board-search input").fill('P4')
            page.locator('#board-search-results mdui-button[data-ref="P4"]').click()
            assert "USART2" in page.locator("#board-detail-title").inner_text()
            page.locator("#board-search input").fill("not-a-part")
            assert page.locator("#board-search-status").inner_text() == "没有匹配的元件"
            page.locator("#board-search input").fill("W25Q64")
            page.locator("#board-search input").press("ArrowDown")
            page.keyboard.press("Enter")
            assert page.locator("#board-detail-ref").inner_text() == "U15"
            before_target = page.evaluate("view.model.controls.target.toArray()")
            page.locator("#board-locate").click()
            assert page.evaluate("view.model.controls.target.toArray()") != before_target
            page.locator("#board-fit").click()
            # Independent schematic literals: Flash on sheet 1, protection and
            # heater on sheet 2, camera supply on sheet 6. These were generic parts.
            for ref, title, value, sheet in [
                ("U15", "W25Q64JV", "W25Q64JV", 1),
                ("U7", "USB 静电保护", "PRTR5V0U2X", 2),
                ("U19", "摄像头 2.8 V 稳压器", None, 6),
                ("R77", "贴片电阻", "470R/0402/1%", 2),
            ]:
                page.locator("#board-search input").fill(ref)
                page.locator(f'#board-search-results mdui-button[data-ref="{ref}"]').click()
                assert page.locator("#board-detail-ref").inner_text() == ref
                assert title in page.locator("#board-detail-title").inner_text()
                assert f"原理图第 {sheet} 页" in page.locator("#board-detail-note").inner_text()
                if value:
                    assert page.locator("#board-detail-state").inner_text() == value
                else:
                    assert page.locator("#board-detail-state").is_hidden()
            assert "加热电阻阵列" in page.locator("#board-detail-description").inner_text()
            page.screenshot(path=str(args.output / "board-schematic-part.png"), animations="disabled")
            # A passive part stays selected after visiting the 2D view.
            page.locator('#board-mode mdui-segmented-button[value="2d"]').click()
            page.locator('#board-mode mdui-segmented-button[value="3d"]').click()
            assert page.evaluate("view.selected.ref") == "R77"
            assert page.evaluate("view.model.selection.visible")
            page.locator("#board-search input").fill('LED3')
            page.locator('#board-search-results mdui-button[data-ref="LED3"]').click()
            assert "WS2812" in page.locator("#board-detail-title").inner_text()
            for _ in range(2):
                page.locator("#board-zoom-in").click()
            page.screenshot(path=str(args.output / "board-3d-detail.png"))
            # The same snapshot is supplied before and after a renderer is ready.
            page.evaluate("snapshot={status:'running',board:{vin_mv:24000,key:{pressed:false},led:{rgb:0x102030,brightness:48,source:'ws2812-din'}},pins:[]};view.update(snapshot,true,channels)")
            assert page.locator("#board-model").get_attribute("aria-label") == "三维板卡，LED #102030"
            assert page.evaluate("view.model.ledMaterials[0].emissive.toArray()") == [16/255, 32/255, 48/255]
            page.evaluate("snapshot={status:'idle',board:null,pins:[]};view.update(snapshot,true,channels)")
            page.locator('#board-mode mdui-segmented-button[value="2d"]').click()
            led = page.locator("[data-led-core]")
            assert led.get_attribute("aria-label") == "LED 无数据"
            assert page.locator("#board-observation").inner_text() == "等待观测"
            page.screenshot(path=str(args.output / "board-idle-1366.png"))
            page.locator("#board-part-flash").click()
            assert page.locator("#board-detail-ref").inner_text() == "U15"
            assert page.locator("#board-detail-state").inner_text() == "W25Q64JV"
            assert "原理图第 1 页" in page.locator("#board-detail-note").inner_text()

            # Literal observations: encoded RGB must not be scaled by brightness again.
            page.evaluate("""() => {
                snapshot={status:'running',observation_stale:false,board:{timestamp_ns:'2000000000',
                    vin_mv:24000,key:{pressed:false},led:{rgb:0,brightness:0,source:'ws2812-din'}},
                    pins:[{pin:'A7',mode:2,level:0}]};
                view.update(snapshot,true,channels);
            }""")
            page.locator("#board-part-led").click()
            assert led.evaluate("e=>getComputedStyle(e).fill") == "rgb(0, 0, 0)"
            assert "#000000" in page.locator("#board-detail-state").inner_text()
            page.evaluate("snapshot.board.led={rgb:0x102030,brightness:48,source:'ws2812-din'};view.update(snapshot,true,channels)")
            assert led.evaluate("e=>getComputedStyle(e).fill") == "rgb(71, 99, 120)"
            assert "WS2812 引脚波形" in page.locator("#board-detail-state").inner_text()
            page.evaluate("snapshot.board.led.source='gpio-latch';snapshot.status='paused';view.update(snapshot,true,channels)")
            assert "GPIO 电平" in page.locator("#board-detail-state").inner_text()
            assert page.locator("#board-observation").inner_text() == "已暂停 · 保留观测"
            page.evaluate("snapshot.observation_stale=true;view.update(snapshot,true,channels)")
            assert "已过期" in page.locator("#board-detail-state").inner_text()
            page.evaluate("view.update(snapshot,false,channels)")
            assert page.locator("#board-observation").inner_text() == "连接已断开"
            assert led.evaluate("e=>getComputedStyle(e).fill") == "rgb(71, 99, 120)"

            # Independent mapping from the manufacturer's V1.1 appendix, including
            # the shared XT30/CAN sockets and the UART5 receive-only SBUS port.
            mapping = {"uart1": "uart1", "uart2": "uart2", "uart3": "uart3", "uart7": "uart7",
                       "uart10": "uart10", "uart5": "uart5", "out1": "can1", "can1": "can1",
                       "out2": "can2", "can2": "can2", "can3": "can3"}
            for component, endpoint in mapping.items():
                page.locator(f"#board-part-{component}").click()
                assert page.evaluate("actions.at(-1)") == {"endpoint": endpoint, "intent": "select"}
            page.evaluate("channels.uart=['uart1'];view.update(snapshot,true,channels)")
            page.locator("#board-part-uart2").click()
            assert "未启用" in page.locator("#board-detail-state").inner_text()
            assert page.locator("#board-detail-action").inner_text() == "打开设置"
            page.locator("#board-detail-action").click()
            assert page.evaluate("actions.at(-1).intent") == "action"

            # Physical pin controls retain keyboard focus across observation updates.
            page.locator("#board-part-led").click()
            pin = page.get_by_role("button", name="PA7", exact=True)
            pin.focus()
            page.evaluate("snapshot.pins[0].level=1;view.update(snapshot,true,channels)")
            assert page.evaluate("document.activeElement.textContent") == "PA7"
            assert "复用 · 1" in page.locator("#board-pin-list").inner_text()
            page.keyboard.press("Enter")
            assert page.evaluate("actions.at(-1).intent") == "gpio"

            # Zoom/pan do not move hotspots away from their physical component.
            for _ in range(3):
                page.locator("#board-zoom-in").click()
            before = page.locator("#board-scene").bounding_box()
            viewport = page.locator("#board-viewport").bounding_box()
            page.mouse.move(viewport["x"] + 8, viewport["y"] + 8)
            page.mouse.down()
            page.mouse.move(viewport["x"] + 38, viewport["y"] + 28)
            page.mouse.up()
            after = page.locator("#board-scene").bounding_box()
            assert abs(after["x"] - before["x"] - 30) < 1 and abs(after["y"] - before["y"] - 20) < 1
            page.locator("#board-part-led").locator("button").focus()
            geometry = page.evaluate("""() => {
                const a=document.querySelector('[data-component="led"]').getBoundingClientRect();
                const b=document.querySelector('#board-part-led').getBoundingClientRect();
                return {dx:Math.abs(a.x-b.x),dy:Math.abs(a.y-b.y),dw:Math.abs(a.width-b.width)};
            }""")
            assert all(value < 1 for value in geometry.values()), geometry
            page.locator("#board-fit").click()
            assert page.locator("#board-zoom").inner_text() == "100%"
            for width, height in [(1080, 720), (390, 844)]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function("width=>document.documentElement.scrollWidth===width", arg=width)
                page.locator("#board-fit").click()
                box = page.locator("#board-scene").bounding_box()
                assert box["width"] > 240, box
                page.screenshot(path=str(args.output / f"board-{width}.png"), full_page=True)
            page.locator('#board-mode mdui-segmented-button[value="3d"]').click()
            page.locator("#board-fit").click()
            assert page.evaluate("document.documentElement.scrollWidth") == 390
            page.screenshot(path=str(args.output / "board-3d-390.png"), full_page=True)

            # Actual WebGL context creation failure keeps the existing 2D controls usable.
            fallback = browser.new_page()
            fallback.route("http://board.test/**", asset)
            fallback.add_init_script("""const original=HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext=function(kind,...args){
                    return kind.startsWith('webgl') ? null : original.call(this,kind,...args);
                };""")
            fallback.goto("http://board.test/")
            fallback.evaluate("async()=>{window.view=new BoardView(()=>{});await view.ready}")
            assert fallback.locator("#board-model-error").is_visible()
            assert fallback.locator("#board-part-uart1").is_visible()
            fallback.close()
            if args.preview:
                offline = browser.new_context(offline=True, viewport={"width": 1440, "height": 960})
                preview = offline.new_page()
                preview.on("pageerror", lambda error: report["errors"].append(str(error)))
                preview.goto(args.preview.resolve().as_uri())
                preview.locator('#model[data-ready="true"]').wait_for(timeout=30000)
                assert preview.locator("#loading").is_hidden()
                preview.locator("#part-select").click()
                preview.locator('#part-select mdui-menu-item[value="P1"]').click()
                assert preview.locator("#part-name").inner_text() == "SWD · 调试接口"
                preview.locator("#bottom").click()
                preview.screenshot(path=str(args.output / "preview-bottom.png"))
                preview.locator("#perspective").click()
                preview.screenshot(path=str(args.output / "preview.png"))
                preview.locator("#part-select").click()
                preview.locator('#part-select mdui-menu-item[value="R77"]').click()
                assert preview.locator("#part-ref").inner_text() == "R77"
                assert preview.locator("#part-value").inner_text() == "470R/0402/1%"
                assert "加热电阻阵列" in preview.locator("#part-description").inner_text()
                assert preview.locator("#part-source").inner_text() == "原理图 · 第 2 页"
                preview.screenshot(path=str(args.output / "preview-schematic-part.png"), animations="disabled")
                with preview.expect_download() as download:
                    preview.locator("#download").click()
                assert Path(download.value.path()).read_bytes() == (ROOT / 'src/models/dm-mc02.glb').read_bytes()
                offline.close()
            assert not report["errors"], report["errors"]
            report["status"] = "passed"
        except BaseException as error:
            report["error"] = repr(error)
            page.screenshot(path=str(args.output / "failure.png"))
            raise
        finally:
            browser.close()
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
