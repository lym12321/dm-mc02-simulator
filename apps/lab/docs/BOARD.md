# DM-MC02 板卡视图与资料

首页展示 DM-MC02 三维装配。启动后，默认拖动板体改变姿态并输入 BMI088；“转视角”调整相机，“放平”恢复水平姿态。
滚轮缩放，右键平移，“俯视”“背面”“适应窗口”调整观察位置；也可切换二维视图。
点击元件，或按位号、型号、UART/CAN 名称搜索，可查看元件信息并定位到模型上的实际位置。
串口和 CAN 面板可停靠在板卡下方、拖动分隔条调整高度，也可展开为独立工具页；切换时保留端口、草稿和收发记录。

## 操作板卡

在模型上的 USER、RESET 按键处按住鼠标、触摸，或聚焦后按空格/Enter，即可按下；松开、失焦、切换视图或离页时释放。
USER 接 PA15。RESET 拉低 NRST，CPU 保持复位直到松开，随后从复位向量启动；QEMU 进程保持不变。

新项目默认使用“板卡姿态”输入。板体绕 PCB 中心转动，运行时按虚拟时间生成连续的 100 ms 轨迹，
计算角速度、重力投影及 BMI088 安装位置处的切向与向心加速度。静止倾斜后，加速度方向随板体变化，角速度归零。
暂停时姿态与输入时间冻结。设置中的“固定静止”保留原来的静止输入，“关闭”交由其它输入来源处理。
旧项目未填写 `imu` 时继续使用 `stationary`；新项目写入 `board_pose`。

LED3 从 PA7 的 DIN 电平和 QEMU 虚拟时间解码，GPIO 翻转、TIM PWM 和 SPI1 MOSI 共用一个 WS2812 状态机。
完整 GRB 帧在复位低电平结束时锁存；详情保留原始 RGB。画面将通道值作为线性光强，转换为屏幕显示色，
三维灯体带自发光、局部照明和光晕。例如 RGB `1,4,8` 对应 sRGB 显示色 `13,34,50`；亮度字段不再次乘到 RGB 上。

BUZZER1 使用 PB15/TIM12_CH2 的频率和占空比，GPIO 连续翻转也可产生音调。
声音按带时间戳的事件播放，保留两次页面刷新之间的短音。“声音”面板控制开关和音量，设置保存在本机浏览器配置中，跨后台端口生效。
暂停、停止、断连和过期观测会停声；声音事件出现缺口时显示错误并停声，重新启动会话恢复。

## 引脚时序与资料

PA7 路由依据 ST DS13313 rev5 Table 8：TIM1_CH1N/AF1、TIM3_CH2/AF2、TIM8_CH1N/AF3、SPI1_MOSI/AF5。
TIM 使用当前时钟、PSC/ARR/CCR、极性、预装载和互补死区产生电平；SPI1 使用 RCC 内核时钟、MBR、数据位宽、位序及帧间隔串行输出。
SPI1 TX 提供 16-byte FIFO、4–32-bit 帧、packed writes、EOT/IRQ35，以及两个 DMA controller 上的 DMAMUX request 38。
GPIO 的推挽、开漏和上下拉决定最终 DIN 电平。无效脉冲、半帧和断开保留最后锁存颜色并计入 `errors`；供电变化清空锁存状态。

原理图将 LED3 标为 `WS2812B-3528`。当前时序配置名为 `worldsemi-ws2812b-classic`，依据
[Worldsemi WS2812B 数据手册](https://cdn-shop.adafruit.com/datasheets/WS2812B.pdf)：

| 电平段 | 时间 |
| --- | --- |
| T0H / T0L | 400 ± 150 ns / 850 ± 150 ns |
| T1H / T1L | 800 ± 150 ns / 450 ± 150 ns |
| 复位低电平 | ≥ 50 µs |
| 数据 | GRB，各字节高位先发；板上第一颗灯消费前 24 bit |

`3528` 料号对应的具体手册修订仍待核对；该配置明确采用上述参考时序。
光效与声音按数字输出呈现，光谱、光通量和蜂鸣器声压使用视觉与音色近似。

板体坐标为俯视右方 +X、上方 +Y、元件面 +Z。依据 CAD 一脚标记及 Bosch BMI088 DS000-19 rev1.9 Figure 12，
传感器 +X 对应板体 +Y，传感器 +Y 对应板体 −X，+Z 一致。
传感器相对 PCB 中心偏移为 `(0.002528006554, -0.017035006523, 0.001436120596)` m；运行时输出 sensor-local `rad/s` 与 `m/s²`，由既有 BMI088 输入通道量化。

## 元件目录

`tools/board-model/parts.json` 保存逐位号元件信息，生成时写入 GLB 节点，首页与独立预览共同使用。
目录包含 293 个 CAD 部件，其中 276 个位号与原理图对应，257 个部件附有型号或参数。
二维 SVG 中的对应器件文案、位号和参数同步保存，热点的端口、引脚与操作字段由 SVG 提供。

| 位号 | 名称与用途 | 原理图页码 |
| --- | --- | --- |
| U14 | STM32H723VGT6 主控 | 1 |
| U15 | W25Q64JV，8 MiB QSPI Flash | 1 |
| U18 | BMI088 六轴惯性传感器 | 2 |
| U1 / U3 / U16 | CAN1 / CAN2 / CAN3 收发器 | 4 |
| U5 / U6 | USART2 / USART3 RS485 收发器 | 5 |
| U7 | PRTR5V0U2X，USB 静电保护 | 2 |
| U11 | BL9342，系统 5 V 电源 | 3 |
| U19 / U20 | 摄像头 2.8 V / 1.5 V 稳压器 | 6 |
| Q9、R77–R84、R88–R89 | CJ2310 开关与十个 470 Ω 加热电阻 | 2 |
| FPC1 | DCMI 摄像头接口 | 6 |
| U13 | 12 针 SPI / I²C 扩展接口，含 PA5 输入与系统 5 V 供电 | 5 |

原理图为用户提供的 `CtrBoard-H7_V1.0-240124.pdf`，共 6 页，日期 2024-01-24，
SHA-256 `3c1ca47e807d6c4031aac10af605021e85e47bd56c65f664543bb947ad35595d`。
电阻、电容参数按位号与图中文字逐项核对，电路用途依据图中连线。

## 三维模型

模型来自用户提供的 `CtrBoard-H7_V1.0-240112A.step`，STEP 文件头日期 `2024-01-12T14:21:28`，
Open CASCADE 7.5，单位毫米。原文件 39,113,347 bytes，
SHA-256 `cd8055098363ca79a72e8fdbf727b2685b54db5bb20c29e531e0ca1b0ff18a07`。

`src/models/dm-mc02.glb` 包含 293 个命名部件、924,776 个三角面，保留正反面器件和连接器内部结构。
转换时移除了同一部件内 6 个完全相同的重复网格。GLB 使用米、Y 向上；PCB 中心 X=0，USB 位于 -Z 一侧。
完整尺寸、转换参数与文件指纹位于相邻的 `dm-mc02.json`。

OpenCascade 细分参数为 0.025 mm 线性偏差与 0.2 rad 角偏差；PBR 材质表现 PCB、外壳及触点的表面效果。
Meshopt 压缩合并逐位相同的顶点属性、重排存储顺序，并让器件共享字节完全相同的属性和索引数组。
这些数组从 1,137 份减少到 588 份，保留已有网格的坐标和法线精度、三角面绕序、材质、节点变换及元件信息。
模型从 12,219,148 bytes 缩至 9,308,948 bytes，约 9.31 MB，再减少 23.8%；首页和独立预览内置解码器。

浏览器使用固定 Three.js 0.180.0 的 GLTFLoader、OrbitControls、Raycaster 和 RoomEnvironment，模型在操作或状态变化时重绘。
相机与板体姿态分别处理。USER、RESET 控件跟随 CAD 按键位置，输入由同一个会话控制器管理。

## 重建与独立预览

在仓库根目录执行（需要 Node.js）：

```sh
cd apps/lab/tools/board-model
npm ci
npm run bundle
node convert.mjs /path/to/CtrBoard-H7_V1.0-240112A.step ../../src/models/dm-mc02.glb
cd ../../../..
node apps/lab/tools/board-model/preview.mjs build/board-3d-delivery
```

更新 `parts.json` 后，可直接把元件信息写入已有 GLB，完整保留其几何二进制数据。输入模型旁需有同名来源 JSON：

```sh
node apps/lab/tools/board-model/annotate.mjs /path/to/input.glb apps/lab/src/models/dm-mc02.glb
node apps/lab/tools/board-model/preview.mjs build/board-3d-delivery
```

同时同步 `src/board.svg` 中对应器件的文案与 `data-ref`、`data-value`、`data-schematic-page` 属性，保留原有热点几何和操作字段。

生成目录中的 `dm-mc02-preview.html` 内嵌模型、脚本与样式，可直接用当前 Chrome / Edge 离线打开。
它支持元件选择、旋转、缩放、正反面查看和 GLB 下载；`dm-mc02-parts.json` 提供可单独读取的元件目录。
浏览器通过原生 `DecompressionStream` 解开 HTML 内的 gzip 数据，下载按钮输出完整 GLB。

模型采用 `EXT_meshopt_compression`。需要普通 GLB 时，在 `apps/lab/tools/board-model` 执行：

```sh
node compress.mjs /path/to/compressed.glb /path/to/uncompressed.glb --decode
```

压缩已有普通 GLB 及核对几何精度的命令：

```sh
node compress.mjs /path/to/original.glb /path/to/compressed.glb
node verify-compression.mjs /path/to/original.glb /path/to/compressed.glb /path/to/fidelity.json
```

开发工具固定 `occt-import-js 0.0.23`、`three 0.180.0`、`esbuild 0.25.11`、
`@gltf-transform/core/extensions/functions 4.5.1` 和 `meshoptimizer 1.3.0`，版本由 npm 锁文件保存。
Three 与 Meshopt 的许可证及来源位于 `src/vendor/licenses/`；CAD 与生成几何的权利归原权利人。

## 二维视图与接口

`board.svg` 是按 DM-MC02 正面布局绘制的原创二维图，板卡尺寸比例为 56 × 40 mm，USB 朝上、VIN 朝左、USER 朝下。
连接器、主控、IMU、Flash、指示灯、按键及安装孔的位置参考厂商资料；材质与阴影由绘图表现。

| 物理接口 | 工具端口 | 操作 |
| --- | --- | --- |
| USART1、UART7、USART10 | `uart1`、`uart7`、`uart10` | TTL 串口收发 |
| USART2-485 / A2,B2 | `uart2` | RS485，PD4 发送使能 |
| USART3-485 / A1,B1 | `uart3` | RS485，PB14 发送使能 |
| SBUS / PD2 | `uart5` | 向板端接收通道发送原始字节 |
| CAN1 侧边与上方 XT30 2+2 | `can1` | 共用 CAN1 总线 |
| CAN2 侧边与下方 XT30 2+2 | `can2` | 共用 CAN2 总线 |
| CAN3 插口 | `can3` | 与 24Pin 扩展口共用 CAN3 总线 |
| SPI / I²C 扩展 PA5 | ADC1 CH18 | 打开 ADC 输入面板 |

引脚详情显示公共 `pins` 中的模式和采样电平。LED 显示锁存颜色及“WS2812 引脚波形”来源。
USER、RESET、VIN 使用板卡快照，USB 显示会话连接状态。暂停时保留观测，停止或断连后的数据标记为过期。

## 外观参考资料

达妙资料仓库：[kit-miao/dm-mc02](https://gitee.com/kit-miao/dm-mc02/tree/0ad8a919495c6da38f36b9352ca1118e490ef4c2)，固定提交 `0ad8a919495c6da38f36b9352ca1118e490ef4c2`。

| 文件 | 用途 | Git blob SHA-1 |
| --- | --- | --- |
| `说明书/达妙科技DM-MC-Board02电机开发板使用说明书V1.1.pdf`，2025-07-23 | 第 2 页尺寸、附录第 15 页布局和引脚、接口章节 | `0d077247714b316d806692df125a6db409d463d1` |
| `图片/DM_H7.png` | PCB、连接器与元件外观参考 | `eae7f5ee959e56f6972daf4177383999f70f2e1b` |

SBUS 采用原理图与手册附录一致的 PD2 / UART5_RX。Flash 型号按原理图 U15 的 W25Q64JV，容量按手册功能章节的 8 Mbytes。
DAMIAO、ST、AMASS 等标识属于各自所有者。
