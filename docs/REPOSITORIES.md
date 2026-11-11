# 两个仓库如何协作

`dm-mc02-simulator` 交付完整仿真软件，Tauri Lab 是桌面入口。`qemu-dm-mc02` 交付可独立构建的 QEMU 引擎。依赖方向为 Simulator → 固定 QEMU，引擎构建不读取 Simulator 源码。

| 内容 | 仓库 |
| --- | --- |
| QEMU 上游源码、板卡、寄存器、外设、IRQ、QMP 扩展 | `qemu-dm-mc02` |
| QEMU 原生测试、引擎构建、NOR 实现、C wire/payload codec | `qemu-dm-mc02` |
| Rust 仿真运行时、CLI、场景、物理世界、设备集成 | `dm-mc02-simulator` |
| Tauri 窗口、Lab 界面、项目/固件交互、桌面验收 | `dm-mc02-simulator/apps/lab` |
| 完整软件打包、依赖闭包、许可证和对应源码收集 | `dm-mc02-simulator` |

QEMU 的 `cosim/` 是 wire/payload codec 的唯一源码。Simulator 的 Rust 构建和原生测试从 `qemu/upstream/cosim/` 编译同一份实现；上层 transport/protocol 留在 Simulator。

## CLI 与 GUI

`sim` 是独立命令行工具，负责固件终端、交互命令、调试配置、场景和自动化。Tauri GUI 管理自己的 `sim-lab-backend` 后台进程，通过内部 loopback 协议操作相同的仿真运行时。HTTP/WebSocket 只是 GUI 的内部传输，不提供 `sim lab web` 或 `sim lab serve` 命令。GUI 源码和资源位于 `apps/lab/`，CLI 不嵌入界面。

## 更新引擎

1. 在 QEMU 仓库修改并验证原生实现，提交后推送到 `dm-mc02/v8.2.2` 或后续引擎分支。
2. 在 Simulator 的 `qemu/upstream` 检出该提交，更新 `qemu.lock` 的 `fork_commit`，与子模块 gitlink 一起提交。
3. 构建 Simulator，验证受影响的 CLI、桌面或场景，再生成完整软件包。

发行包记录两个仓库的提交身份，并附对应源码。未提交源码只能使用显式 development 包。

## 迁移与历史

Simulator 继承原 `refactor/composable-platform` 的历史，桌面源码从本地 Tauri Lab 导入；Windows 修复随各自实现迁入两个仓库。旧平台 checkout 和独立 Lab checkout 保留作为恢复来源。

原远端的 `main`、`refactor/composable-platform` 和真正的 QEMU 分支曾混在同一仓库。拆分沿用现有 QEMU 分支作为引擎主线，保留原分支历史；远端默认分支调整和旧分支归档属于后续发布操作。
