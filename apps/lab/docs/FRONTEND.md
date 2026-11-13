# Lab 界面与后端协作

`apps/lab/src` 是 Simulator 桌面界面的源码。Rust 后端负责 ELF 加载、QEMU 生命周期、LabSession、项目配置和 HTTP/WebSocket 协议；Tauri 负责窗口、后端启动及退出清理。

## 板卡首页

启动应用默认显示 DM-MC02 的精细三维装配，可切换到二维俯视图。打开 ELF 后，使用顶部按钮启动、暂停、继续或停止。
选择板上的串口、RS485、CAN 接口后，点击详情中的操作按钮可打开停靠面板；“展开到工具页”使用完整窗口，收发记录和发送草稿继续保留。
未启用的接口显示设置入口，设置变更在下一次启动生效。

USER 和 RESET 支持鼠标、触摸或空格/Enter 按住操作，松开、失焦、切换视图或离开板卡页会释放。RESET 保持 NRST，松开后 CPU 从复位向量启动。
RGB 灯、VIN、按键和 USB 显示当前状态；点击器件查看名称、型号、参数、电路用途和原理图页码，点击引脚名称打开 GPIO 控制。
电源入口、USB 接口可打开对应面板，SPI / I²C 扩展接口提供 ADC1 CH18 输入入口，在运行状态下施加输入。
暂停保留观测，停止或断连后的旧数据标记为过期；未获得观测与实际零值分开显示。

三维视图默认“动板子”：启动后拖动改变姿态并输入 IMU；“转视角”单独调整相机，“放平”恢复水平姿态。
滚轮缩放，右键平移，可一键俯视或查看背面；搜索框按位号、型号和端口查找 293 个部件，“定位元件”放大所选部件。
“声音”面板提供蜂鸣器开关和音量。页面在启动操作时开启 Web Audio，暂停、停止或断连时停声；音量和静音用同一浏览器配置中的 `dm_mc02_audio` 持久 cookie 保存，覆盖后台随机 loopback 端口。
二维视图拖动平移，用 `+` / `−` 或 Ctrl/⌘+滚轮缩放，“适应窗口”恢复完整板卡。
顶部“重载”重新加载固件并重建会话。
外观资料和端口对应关系见 [BOARD.md](BOARD.md)。

## 开发界面

界面入口为 `src/index.html`、`src/style.css` 和 `src/app.js`；`src/board-view.js` 与 `src/board.svg` 提供板卡展示，`src/uart-console.js` 管理终端。SVG 中的组件几何同时确定热点位置，`data-*` 属性声明名称、端口和已确认的 MCU 引脚。桌面启动时，后端从资源目录预加载界面文件；修改后需重启应用。界面资产随桌面应用一起打包，Rust CLI 不内嵌界面。

浏览器验收可以直接启动桌面的私有后台。从 `apps/lab` 执行：

```powershell
& '..\..\target\release\sim-lab-backend.exe' --port 0 --assets-dir "$PWD\src"
```

界面、HTTP 与 WebSocket 使用同一个 `http://127.0.0.1:<port>` 地址。成功启动时 stdout 给出 `lab-ready` JSON，协议标识为 `dm-mc02-lab/v1`；日志写入 stderr。保持 stdin 打开，关闭 stdin 会让后台结束实验并退出。`GET /api/info` 提供协议版本和能力，完整约定见仓库根目录的 [LAB_PROTOCOL.md](../../../docs/LAB_PROTOCOL.md)。

界面使用 `/api/state`、`/api/project`、`/api/firmware`、`/api/ide/save` 和 `/api/ws`。IDE 保存请求只含格式和 LabProject，后台负责生成配置、打开原生另存为窗口并写入。`/api/ide` 保留配置预览接口。WebSocket 命令保留 `{id, session_id, action, args}` 封套；会话代次和命令结果由后端确定。连接断开时界面保留记录、清除待处理请求并重连。通过“打开 ELF”选择固件；本地路径与自动重载在“设置”中配置。

`src/board-model.js` 使用离线 Three.js 加载 `src/models/dm-mc02.glb`。CAD 节点拥有三维几何和位号；
`tools/board-model/parts.json` 是器件名称、型号、参数、用途和原理图来源的目录，写入 GLB 后由首页与预览读取，二维 SVG 同步对应文案。
BoardView 复用 SVG 的端口、引脚和操作字段，以及 app.js 的同一输入 owner。三维加载失败时保留可操作的二维视图并显示原因。
模型转换和独立离线预览生成命令见 [BOARD.md](BOARD.md)。

## 离线资源与许可证

页面只引用本地资源，`src/vendor` 提供后端固定资源名单中的文件：

| 资源 | 来源与版本 | 许可证、来源记录 |
| --- | --- | --- |
| `mdui.js`、`mdui.css` | mdui 2.1.4 | `src/vendor/licenses/mdui/` |
| `xterm.js`、`xterm.css` | @xterm/xterm 5.5.0 | `src/vendor/licenses/xterm/` |
| `addon-fit.js` | @xterm/addon-fit 0.10.0 | `src/vendor/licenses/xterm/` |
| `three.js` | three 0.180.0（官方模块经 esbuild 打包） | `src/vendor/licenses/three/` |
| 同一 `three.js` 内嵌解码器 | meshoptimizer 1.3.0（包含 WASM，无外部请求） | `src/vendor/licenses/meshoptimizer/` |

升级组件时同时更新许可证、notices 和 `PROVENANCE.json`。MDUI/xterm 保持上游发行文件；Three 和 Meshopt 的未改动模块由 `tools/board-model/bundle.mjs` 打包。
模型通过无量化 Meshopt 压缩交付；存储格式与几何精度验证、普通 GLB 解压命令见 [BOARD.md](BOARD.md)。

## 浏览器验收

`tests/lab_board_browser.py` 验证实际 CAD/WebGL 装配加载、拾取、旋转、视图切换、WebGL 失败回退；可用 `--preview <HTML路径>` 验证断网时直接打开独立文件及 GLB 下载。它还使用固定快照验证 RGB、未知/过期/暂停显示、端口对应、焦点保持和缩放热点。`tests/lab_uart_console_browser.py` 验证真实 xterm 的跨 chunk UTF-8、ANSI、HEX、独立 UART、清空与积压恢复。

`tests/lab_console_browser.py` 使用真实后台和固件，验证姿态拖动、相机独立、USER→SPI/DMA→LED、RESET、2 kHz 蜂鸣器、低亮度颜色和声音设置，以及 UART/CAN 停靠、ADC、固件上传及会话生命周期。`tests/lab_audio_browser.py` 用真实 OfflineAudioContext 生成 PCM，检查频率、短音时长、音量与停声。后端的 HTTP/WS、控制权及生命周期测试在根目录 `crates/sim-host/tests/`。

`tests/lab_ide_export_browser.py --backend <后台程序> --elf <本地ELF> --output build/ide-export` 拦截专用保存请求，验证三种格式的请求内容，以及保存、取消、写入失败和缺少调试条件时的界面反馈，默认使用已安装的 Microsoft Edge。配置文本和原子写入由后台单元测试验证；真实原生另存为窗口与文件内容验收见 [DESKTOP.md](DESKTOP.md)。

从 `apps/lab` 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests\lab_uart_console_browser.py --output build\uart-browser
.\.venv\Scripts\python.exe tests\lab_board_browser.py --output build\board-browser
.\.venv\Scripts\python.exe tests\lab_console_browser.py `
  --backend '..\..\target\release\sim-lab-backend.exe' `
  --qemu '..\..\build\qemu\qemu-system-arm.exe' `
  --elf '..\..\build\lab\lab.elf' --output build\console-browser
```

控制台完整断言使用根目录 `smoke/dm_mc02_lab_smoke.c` 的 `LAB_READY` 固件。其它固件不能替代该 fixture 的断言。已安装 Microsoft Edge 时可加 `--browser-channel msedge`，无需下载 Chromium。输出目录包含截图和 JSON 结果。

完整的进程合同验收在根目录 `crates/sim-host/tests/desktop_backend_native.py`，覆盖固定资源名单、Origin、readiness 以及 EOF/LF/CRLF 关闭。`tests/desktop_transport_native.py` 保留 HTTP/WS 控制权、上传、会话代次和信号清理的 Linux 原生验收；`lab_hot_reload_browser.py`、`lab_firmware_replace_browser.py` 与 `lab_startup_browser.py` 覆盖本地固件更新、显式重载和启动中取消，均以 `--backend` 指定私有后台。使用 QMP Unix socket 或 shell fixture 的这些案例在 Linux 运行。
