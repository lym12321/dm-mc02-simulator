# DM-MC02 Simulator

运行 DM-MC02 / STM32H723 固件的桌面仿真软件。打开 ELF，观察 UART、CAN、ADC 和板卡状态，暂停、重载固件，或连接 ARM GDB 调试。

Tauri Lab 是软件的主界面，与 Rust 仿真运行时、CLI、场景和发行工具一起维护在本仓库。[qemu-dm-mc02](https://github.com/lym12321/qemu-dm-mc02/tree/dm-mc02/v8.2.2) 提供独立的 QEMU 引擎，由子模块和 `qemu.lock` 固定版本。

## 打开软件

在 Windows 便携包中双击 **DM-MC02 Simulator.exe**，选择 ELF 后启动。暂停、恢复、停止和重载都在主界面中完成；关闭窗口会同时停止所属仿真进程。

保留 EXE 旁的 `runtime/`、`lab/` 和 DLL。运行需要 Windows WebView2；不需要安装 Rust、Node.js、Python 或 MSYS2。发行时同时提供对应源码包，许可证随运行包保留。

桌面功能见 [桌面说明](apps/lab/docs/DESKTOP.md)，运行时与场景见 [使用手册](docs/USAGE.md)。当前模型用于固件功能验证；Windows 的实时倍率和内存问题仍在分析中。

## Windows 开发

先启用 Windows 开发者模式，再检出源码和固定 QEMU：

```powershell
git clone -c core.symlinks=true https://github.com/lym12321/dm-mc02-simulator.git
cd dm-mc02-simulator
git submodule update --init --depth 1 qemu/upstream
```

本项目已使用 Rust GNU / MinGW 验证。准备 Rust、Node.js、WebView2 和 MSYS2 MINGW64 后，从仓库根目录构建：

```powershell
. .\tools\Enter-Dev.ps1 -MsysRoot D:\Environment\msys64
.\tools\Build-Simulator.ps1
```

构建入口生成可直接打开的便携目录，并打印 EXE 与对应源码包的位置。开发中的未提交源码请显式加 `-Development`。准备好运行包后，修改界面可运行：

```powershell
npm --prefix apps/lab run dev
```

GUI 的界面源码位于 `apps/lab/src`，专属后台进程为 `sim-lab-backend`。CLI `sim` 提供终端、调试、场景与自动化命令，不包含网页入口或界面资源。详见 [开发与构建](docs/DEVELOPMENT.md)。

## CLI 与场景

CLI 可独立构建，不需要 Node.js 或 Tauri：

```powershell
cargo build --release --locked -p sim-host --features windows-lab --bin sim
.\target\release\sim.exe lab interactive --elf .\app.elf --qemu .\build\qemu\qemu-system-arm.exe
.\target\release\sim.exe firmware .\app.elf --debug --qemu .\build\qemu\qemu-system-arm.exe
```

Linux 的场景、worker、MuJoCo/Gazebo/ROS 2 与实验重放沿用已有构建入口：

```bash
cargo build --release --locked -p sim-host --features host-runtime --bin sim
target/release/sim validate examples/can-faults/host-only.yaml
target/release/sim run examples/can-faults/host-only.yaml --report build/host-only.json
```

真实固件另需构建 QEMU；原生物理 SDK 的安装和功能范围见 [使用手册](docs/USAGE.md)。

## 代码归属

| 路径 | 职责 |
| --- | --- |
| `apps/lab` | Tauri 桌面、界面、浏览器验收、桌面打包 |
| `crates/sim-core` | 生命周期、时间和调度合同 |
| `crates/sim-host` | 仿真运行时、CLI、GUI 专属后台、设备和物理集成 |
| `cosim` | 上层协议和 transport；wire codec 来自固定 QEMU |
| `qemu/upstream` | 独立 QEMU 仓库的固定子模块 |
| `examples`, `firmware`, `sim`, `models` | 示例固件、场景、原生物理集成和资源 |
| `tools`, `tests`, `smoke` | 软件构建、发行和集成验收 |

两仓职责和版本协作见 [仓库拆分](docs/REPOSITORIES.md)，GUI 与后台进程接口见 [Lab 协议](docs/LAB_PROTOCOL.md)。

第三方源码、依赖和模型保留各自来源与许可证；本仓库不分发外部 `trobot` 固件。
