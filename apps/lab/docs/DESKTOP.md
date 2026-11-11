# 桌面开发与验证

本机开发入口由 `cargo create-tauri-app` 创建，保留 Tauri 2 与原生 HTML/JavaScript。界面资源由独立后端提供，Tauri 只管理窗口和后端进程。

## 构建便携目录

先按 `BACKEND_BUNDLE.md` 准备运行包，再执行：

```powershell
npm ci
npm run build
```

`tools/build-desktop.ps1` 调用 Tauri 的 `build --no-bundle --locked`，将程序、WebView2 loader（GNU 构建需要）、后端运行包和界面放入新的 `build/desktop/DM-MC02-Simulator-<时间与标识>/`。`build/desktop/latest.txt` 记录最近一次目录。双击其中的 `DM-MC02 Simulator.exe`，不要单独移动 EXE。

本机使用 Rust 1.99.1 / `x86_64-pc-windows-gnu`、Tauri 2.12.5 和 WebView2，已通过 release 构建及原生窗口验收。Tauri 官方 Windows 发行文档要求 MSVC C++ Build Tools 与 Windows SDK；安装器发布另按该工具链验证。后端独立进程使用 GNU/MinGW，不依赖桌面程序的 Rust ABI。

本目录的 `rust-toolchain.toml` 使用 `stable-x86_64-pc-windows-gnu`，后端根目录继续固定 Rust 1.91.1；构建不修改全局默认工具链。MSVC 环境可先在当前 PowerShell 设置 `$env:RUSTUP_TOOLCHAIN = 'stable-x86_64-pc-windows-msvc'` 后运行上述命令；GNU 环境需让 MinGW 的 `bin` 进入当前 PATH。

可设置 `CARGO_TARGET_DIR` 复用现有 Cargo 构建目录，打包脚本会从同一目录取得产物。

桌面资源中的 `runtime/SOURCE_BUNDLE.txt` 指向匹配的完整后端包。对外分发时一起提供该完整包的对应源码，保留后端和浏览器组件的许可证。这里的本地便携构建不会上传或发布仓库。
桌面依赖和 WebView2 loader 的许可证保留在便携目录 `licenses/desktop/`，来源见 [DEPENDENCIES.md](DEPENDENCIES.md)。

## 生命周期与故障

程序启动 私有后台 `sim-lab-backend --port 0 --assets-dir <资源目录>`，检查 readiness 的事件、协议、PID 和 loopback URL 后显示页面。启动错误会显示本地诊断窗口；后端日志写入 `%LOCALAPPDATA%\dev.dm-mc02.simulator\logs\backend.log`（以错误页显示的实际路径为准）。

正常关闭窗口时写入 `shutdown`，最多等待五秒让后端清理实验；超时后终止 Windows Job Object 中的整个进程树。spawn 与所有权登记使用同一个锁，启动中立即关闭也走相同清理流程。后端进程意外退出时清理所属进程并显示独立的本地诊断页，避免启动导航覆盖错误。

前端页面与后端同源；未配置远程页面的 Tauri IPC 权限。启动器没有给页面提供 shell 或文件系统命令。

## 验证入口

```powershell
npm run check
```

readiness 单元测试检查协议、PID、URL 和无效地址。浏览器验收见 `FRONTEND.md`；它覆盖真实后端下的 UART/CAN/ADC/USER，以及固件上传、暂停、恢复、重载、停止。后端进程合同验收留在同仓根目录的 `crates/sim-host/tests/desktop_backend_native.py`。

原生窗口验收通过实际 WebView2 操作界面，并由测试者关闭标题栏窗口，验证运行中退出和后端快速退出后的诊断页：

```powershell
$desktop = (Get-Content build/desktop/latest.txt -Raw).Trim()
.\.venv\Scripts\python.exe tests/desktop_smoke.py `
  --app (Join-Path $desktop 'DM-MC02 Simulator.exe') --elf 'C:\firmware\tick.elf' `
  --output build\native-desktop --check-early-exit
```

脚本会两次提示关闭本次启动的原生窗口，最多等待三分钟；它核对退出码及本次所属 sim/QEMU 的清理。调试端口仅在测试子进程的环境中启用。`--check-early-exit` 使用一个小型 Rust fixture，因此需要 `rustc`；不指定该参数时只验收真实后端与 ELF。
