# 桌面依赖许可证

`tools/collect-desktop-notices.mjs` 从锁定的 Cargo 依赖收集许可证，写入便携目录的
`licenses/desktop/`。其中的 `dependencies.json` 记录当前目标解析到的运行与构建依赖，
以及每份补充许可证的来源。Rust 标准库的版权与许可证也一并保留。

优先使用 Cargo 包中的 `LICENSE`、`LICENCE`、`COPYING`、`NOTICE` 文件。
以下包的发行源码没有附带独立许可证文件，改用 `third_party/rust-notices/` 中的上游原文：

| 依赖 | 补充内容 |
| --- | --- |
| `alloc-stdlib` 0.3.0 | Dropbox 仓库的 BSD 3-Clause 许可证 |
| `selectors` 0.38.0 | 原始 MPL 2.0 文件头与 Mozilla 官方的完整 MPL 2.0 文本 |
| `tauri-plugin` 2.7.2 | Tauri 仓库的 MIT 与 Apache 2.0 许可证 |
| `webview2-com` 0.39.1、`webview2-com-macros` 0.8.1、`webview2-com-sys` 0.39.1 | webview2-rs 仓库的 MIT 许可证，共用一份原文 |

每个包的源码提交取自其 `.cargo_vcs_info.json`。对应提交、原始文件 URL 和提取范围
记录在 `third_party/rust-notices/PROVENANCE.json`；构建时直接读取本地文件，无需访问网络。
`plist` 的许可证已包含在发行包的 `LICENCE` 中，无需另存上游副本。

Windows WebView2 loader 使用 `webview2-com-sys` 0.39.1 携带的 SDK 1.0.3800.47。
Microsoft 的同版本 NuGet 包将 `LICENSE.txt` 声明为许可证，并附有 `NOTICE.txt`。
两份文件均原样保留在 `licenses/desktop/microsoft-webview2-loader-1.0.3800.47/`，
同时记录 NuGet 来源，覆盖动态 loader 和静态 loader 的分发。

更新依赖后重新运行收集器。若新包没有附带许可证，收集器会指出具体包和版本，
应补上对应上游原文及来源后再打包。
