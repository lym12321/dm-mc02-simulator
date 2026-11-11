# 从源码构建 Simulator

便携包用户直接运行 EXE。源码开发需要 Rust、Node.js、Windows WebView2 和 MSYS2 MINGW64。

## Windows GNU 环境

后端由根 `rust-toolchain.toml` 固定 Rust 1.91.1；桌面由 `apps/lab/rust-toolchain.toml` 单独选择工具链。两者保留自己的 Cargo.lock。MSYS2 MINGW64 提供 GCC、GLib、zlib、pixman、pkg-config、Ninja 与 Python/Meson，供 QEMU 构建和 DLL 打包使用。

Windows 开发者模式用于检出符号链接。克隆时启用 `core.symlinks=true`；GitHub 源码 ZIP 不含固定 QEMU 子模块。

```powershell
git submodule update --init --depth 1 qemu/upstream
. .\tools\Enter-Dev.ps1 -MsysRoot D:\Environment\msys64
.\tools\Build-Simulator.ps1
```

`Enter-Dev.ps1` 设置当前 PowerShell 的开发环境，不切换目录。`Build-Simulator.ps1` 完成引擎、Rust 后台、运行包、桌面和对应源码收集。`-Development` 显式归档未提交源码；正常发行要求提交与子模块 pin 一致。

如已有同一源码构建的 QEMU，可用 `-SkipQemuBuild -QemuPath <exe>` 复用。不要将实验 candidate 或别的提交的二进制混入发行包。

## 界面与运行时开发

准备一次完整后端后，在仓库根执行：

```powershell
npm --prefix apps/lab run dev
```

界面在 `apps/lab/src/`，桌面原生窗口在 `apps/lab/src-tauri/`。GUI 专属后台 `sim-lab-backend` 只为桌面服务，通过 stdin 的 `shutdown` 命令和 loopback HTTP/WebSocket 管理会话；协议见 [LAB_PROTOCOL.md](LAB_PROTOCOL.md)。修改 Rust 后台后重新执行 `Build-Simulator.ps1` 或 `apps/lab/tools/prepare-backend.ps1` 刷新运行包。

CLI 可单独构建，不需要 npm 或 GUI 资源：

```powershell
cargo build --locked --release -p sim-host --features windows-lab --bin sim
.\target\release\sim.exe lab interactive --elf .\app.elf --qemu .\build\qemu\qemu-system-arm.exe
```

## 验证入口

```powershell
cargo test --locked -p sim-host --features windows-lab,desktop-backend --lib
cargo clippy --locked -p sim-host --features windows-lab,desktop-backend --all-targets --no-deps -- -D warnings
npm --prefix apps/lab run check
python -m pytest tests/test_native_package.py tests/test_native_development_package.py -q
```

桌面关键路径使用 `apps/lab/tests/desktop_smoke.py`，覆盖实际 WebView2 中的固件加载、运行/暂停/重载/停止与原生窗口关闭；后台生命周期入口为 `crates/sim-host/tests/desktop_backend_native.py`。两者需要已构建的原生二进制与 ELF。

Linux 原生物理 SDK、场景和完整验收见 [使用手册](USAGE.md)。
