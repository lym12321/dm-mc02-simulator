# Lab 界面与后端协作

`apps/lab/src` 是 Simulator 桌面界面的源码。Rust 后端负责 ELF 加载、QEMU 生命周期、LabSession、项目配置和 HTTP/WebSocket 协议；Tauri 负责窗口、后端启动及退出清理。

## 开发界面

修改 `src/index.html`、`src/style.css`、`src/app.js` 或 `src/uart-console.js`。桌面启动时，后端从资源目录预加载界面文件；修改后需重启应用。界面资产随桌面应用一起打包，Rust CLI 不内嵌界面。

浏览器验收可以直接启动桌面的私有后台。从 `apps/lab` 执行：

```powershell
& '..\..\target\release\sim-lab-backend.exe' --port 0 --assets-dir "$PWD\src"
```

界面、HTTP 与 WebSocket 使用同一个 `http://127.0.0.1:<port>` 地址。成功启动时 stdout 给出 `lab-ready` JSON，协议标识为 `dm-mc02-lab/v1`；日志写入 stderr。保持 stdin 打开，关闭 stdin 会让后台结束实验并退出。`GET /api/info` 提供协议版本和能力，完整约定见仓库根目录的 [LAB_PROTOCOL.md](../../../docs/LAB_PROTOCOL.md)。

界面使用 `/api/state`、`/api/project`、`/api/firmware`、`/api/ide/save` 和 `/api/ws`。IDE 保存请求只含格式和 LabProject，后台负责生成配置、打开原生另存为窗口并写入。`/api/ide` 保留配置预览接口。WebSocket 命令保留 `{id, session_id, action, args}` 封套；会话代次和命令结果由后端确定。连接断开时界面保留记录、清除待处理请求并重连。通过“打开 ELF”选择固件；本地路径与自动重载在“设置”中配置。

## 离线资源与许可证

页面只引用本地资源，`src/vendor` 提供后端固定资源名单中的文件：

| 资源 | 来源与版本 | 许可证、来源记录 |
| --- | --- | --- |
| `mdui.js`、`mdui.css` | mdui 2.1.4 | `src/vendor/licenses/mdui/` |
| `xterm.js`、`xterm.css` | @xterm/xterm 5.5.0 | `src/vendor/licenses/xterm/` |
| `addon-fit.js` | @xterm/addon-fit 0.10.0 | `src/vendor/licenses/xterm/` |

升级组件时同时更新许可证、notices 和 `PROVENANCE.json`。JavaScript/CSS 保持上游发行文件。

## 浏览器验收

`tests/lab_uart_console_browser.py` 验证真实 xterm 的跨 chunk UTF-8、ANSI、HEX、独立 UART、清空与积压恢复。`tests/lab_console_browser.py` 使用真实桌面后台和同仓界面，验证 UART/CAN/ADC/USER、固件上传、暂停、恢复、重载与停止。后端的 HTTP/WS、控制权及生命周期测试在根目录 `crates/sim-host/tests/`。

`tests/lab_ide_export_browser.py --backend <后台程序> --elf <本地ELF> --output build/ide-export` 拦截专用保存请求，验证三种格式的请求内容，以及保存、取消、写入失败和缺少调试条件时的界面反馈，默认使用已安装的 Microsoft Edge。配置文本和原子写入由后台单元测试验证；真实原生另存为窗口与文件内容验收见 [DESKTOP.md](DESKTOP.md)。

从 `apps/lab` 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests\lab_uart_console_browser.py --output build\uart-browser
.\.venv\Scripts\python.exe tests\lab_console_browser.py `
  --backend '..\..\target\release\sim-lab-backend.exe' `
  --qemu '..\..\build\qemu\qemu-system-arm.exe' `
  --elf '..\..\build\lab\lab.elf' --output build\console-browser
```

控制台完整断言使用根目录 `smoke/dm_mc02_lab_smoke.c` 的 `LAB_READY` 固件。其它固件不能替代该 fixture 的断言。已安装 Microsoft Edge 时可加 `--browser-channel msedge`，无需下载 Chromium。输出目录包含截图和 JSON 结果。

完整的进程合同验收在根目录 `crates/sim-host/tests/desktop_backend_native.py`，覆盖固定资源名单、Origin、readiness 以及 EOF/LF/CRLF 关闭。`tests/desktop_transport_native.py` 保留 HTTP/WS 控制权、上传、会话代次和信号清理的 Linux 原生验收；`lab_hot_reload_browser.py`、`lab_firmware_replace_browser.py` 与 `lab_startup_browser.py` 覆盖本地固件更新、显式重载和启动中取消，均以 `--backend` 指定私有后台。使用 QMP Unix socket 或 shell fixture 的这些案例在 Linux 运行。
