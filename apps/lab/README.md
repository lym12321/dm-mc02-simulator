# DM-MC02 Simulator 桌面界面

这是 Simulator 的 Tauri Lab，和 Rust 运行时、CLI、场景及发行工具放在同一仓库。QEMU 板卡与器件实现由独立的 `qemu-dm-mc02` 引擎仓库维护，版本固定在根目录的 `qemu/upstream` 子模块。

发行目录中双击 `DM-MC02 Simulator.exe`，打开 ELF 即可运行、暂停、重载固件，查看 UART/CAN，操作 USER 按键和引脚外设。保留旁边的 `runtime/`、`lab/` 和 DLL。运行时需要 Windows WebView2，无需安装 Rust、Node.js 或 MSYS2。关闭窗口会结束当前实验并清理后端进程。

## 开发界面

本工程由 `cargo create-tauri-app` 的 Tauri 2 / vanilla 模板创建。先安装 Node.js、Rust、WebView2 和后端构建依赖；从仓库根目录进入：

```powershell
cd apps/lab
npm ci
.\tools\prepare-backend.ps1 -MsysRoot D:\Environment\msys64 -Development
npm run dev
```

准备脚本默认使用当前 Simulator 仓库，构建后端的 `windows-lab,desktop-backend` features，并打包固定 QEMU。已有完整后端包时可改用 `-RuntimeDir <完整包目录>`。`-Development` 将未提交的 Simulator 与 QEMU 源码一起归档；正式发布省略此选项。

桌面目录使用自己的 `rust-toolchain.toml` 和 Cargo 锁文件；后端继续使用仓库根目录固定的 Rust 1.91.1。GNU/MinGW 便携构建与官方 MSVC 安装器的工具要求见 [DESKTOP.md](docs/DESKTOP.md)。

界面源码在 `src/`，Tauri 生命周期在 `src-tauri/src/backend.rs`。桌面启动私有的 `sim-lab-backend` 进程管理实验，通过本机 HTTP/WebSocket 协作，协议见仓库根目录的 [LAB_PROTOCOL.md](../../docs/LAB_PROTOCOL.md)。公开的 `sim` CLI 提供终端、固件和场景工具。

## 构建与验收

```powershell
npm run build
npm run check
```

便携构建入口见 [DESKTOP.md](docs/DESKTOP.md)，浏览器验收见 [FRONTEND.md](docs/FRONTEND.md)，运行包和对应源码见 [BACKEND_BUNDLE.md](docs/BACKEND_BUNDLE.md)。第三方浏览器资源的许可证在 `src/vendor/licenses/`，后端许可证随包保留在 `runtime/licenses/`。
