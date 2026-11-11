# 准备桌面后端

桌面 Lab 使用私有进程 `sim-lab-backend.exe` 与 QEMU，Tauri 管理其启动和退出。
后端完整包包含命令行 `sim.exe`、桌面后台 `sim-lab-backend.exe`、`bin/` 中的 QEMU 和 DLL、许可证、版本来源及对应源码。

在 PowerShell 中，从 Simulator 的 `apps/lab` 执行：

```powershell
.\tools\prepare-backend.ps1 -MsysRoot D:\Environment\msys64 -Development
```

脚本默认使用同仓根目录的固定 Rust 工具链构建 `windows-lab,desktop-backend` 的全部二进制，然后调用根目录已有的
`tools/package-qemu.py`。QEMU 默认取后端的 `build/qemu/qemu-system-arm.exe`；
应先按后端手册构建。可用 `-QemuPath`、`-SimPath`、`-DesktopBackendPath` 指定产物，`-NoBuild` 复用已构建的二进制。`CARGO_TARGET_DIR` 同时用于构建与定位 CLI、桌面后台。
`-Development` 将主仓库与 QEMU 当前工作树一并归档；正式发布省略此选项，打包器会执行发布源码检查。
MSYS2 用于构建和收集已安装 DLL 的许可证，准备后的桌面程序无需 MSYS2。

已有完整后端包时，直接接入，不需要后端 checkout、Python 或 MSYS2：

```powershell
.\tools\prepare-backend.ps1 -RuntimeDir C:\packages\dm-mc02-windows-x86_64
```

两种入口都会在最小 `PATH` 下检查私有后台和 QEMU 的 `dm-mc02` 机器。完整包必须包含桌面后台；纯 CLI 包用于终端工具。

## 产物与源码分发

Tauri 资源写入 `src-tauri/runtime/`，其中保留运行文件、`licenses/`、
`provenance.json` 和 `SOURCE_BUNDLE.txt`。旧资源整体移到 `build/runtime-previous-*`，
避免更新后残留旧 DLL。

完整后端包保留在 `build/backend-packages/<时间与标识>/`，其中的 `sources/`
包含 Simulator、QEMU、固定子项目及 Cargo vendor 源码。`provenance.json` 的来源路径
以这个完整包目录为基准。`SOURCE_BUNDLE.txt` 记录本次完整包的位置。
`build/backend-package.json` 提供当前运行目录、完整包与对应源码的位置，供后续桌面打包读取。

发布桌面程序时，应随附匹配的源码包，或提供可取得该源码包的下载位置；保留全部许可证与来源文件。
源码独立于 Tauri 资源，避免每次桌面启动展开源码。开发快照的源码包含未提交改动，不能用基础提交号替代。
