# Orbbec Gemini 2 相机接入

**2026-10-23 更新：**Gemini 2 现为固定第三视角（`scene`），腕部另有 Gemini 305
（`wrist`）。Windows 分别枚举到 `2bc5:0670` 和 `2bc5:0840`；两台均已附加到
WSL 并各完成 5 帧 RGB-D 短采集，双机长时稳定性与标定尚未验收。
双相机录制见 [操作说明](dual-camera-recording.md)。下方 2026-10-15 状态为历史准备记录。

2026-10-15 用户确认使用 **Orbbec Gemini 2**。本文件中的设备是 Gemini 2；
SDK 对 Gemini 2 XL 的支持路线不同，不能互相替代型号。

## 当前状态

- `config/hardware.template.json` 已记录型号，机械臂控制仍为禁用。
- `.venv` 和 `.venv-training` 已安装 `pyorbbecsdk2==2.0.18`，导入名为 `pyorbbecsdk`。
- 当前 Windows/WSL 均未枚举到 Gemini 2。
- Windows OrbbecViewer 2.7.6 已放在 `D:\reBot-tools\OrbbecViewer-v2.7.6\OrbbecViewer.exe`；
  Linux SDK 官方 udev 权限规则已安装。环境复核见 [软件检查](software-environment-check.md)。
- 已实现设备枚举、RGB-D 同步/D2C、去畸变、深度比例与时间戳检查和文件保存。
- 项目自己的 LeRobot 插件注册为 `orbbec_gemini2`，通过专用启动脚本加载，无需修改
  `vendor/lerobot`；已用替代 SDK 和队列数据测试，不代表实物采流已通过。

## 使用的软件和环境

| 工作 | 软件 | 运行位置与结果 |
|---|---|---|
| 首次识别、RGB/深度预览、读取固件与内参 | Orbbec Viewer，来自官方 SDK v2 发布包 | 先用 Windows 原生版；确认设备、彩色与深度均可持续显示 |
| Python RGB-D 采集、D2C 对齐、时间戳与深度单位读取 | 官方 `pyorbbecsdk2==2.0.18` | `.venv` 和 `.venv-training` 已安装，重新安装用 `scripts/bootstrap_camera.sh` |
| 项目视觉和几何处理 | OpenCV、NumPy、现有项目代码 | WSL `.venv`；读取真实图片、深度和实际标定 |
| 示教数据和 ACT | 固定版本 LeRobot、PyTorch | WSL `.venv-training`；同步 RGB、状态、实际动作目标 |

官方 SDK 支持 Windows 和 Ubuntu 22.04。SDK v2 当前支持表列出 Gemini 2 最低固件
`1.4.92`、推荐固件 `1.4.98`（本次查询值）。先通过 Viewer 读取实际固件；旧固件需按
官方兼容表选择匹配的 SDK 或评估升级，当前工程没有自动刷写固件。

安装包名需带数字 **2**。PyPI 的 `pyorbbecsdk` 是旧包；较新的 `pyorbbecsdk2` 2.1.x
把演示软件的 PyAV/NumPy 等约束一起写入依赖，与本工程锁定环境冲突，因此本项目固定
无此依赖冲突的 2.0.18。版本已加入两份环境锁定文件，两个环境的依赖检查通过。

## 已可运行的软件入口

在 VS Code 的 WSL 终端、项目根目录运行：

```bash
# 已安装；换机或缺少 SDK 时重新初始化
bash scripts/bootstrap_camera.sh

# 只验证软件，不要求相机连接
bash scripts/camera.sh check

# 枚举实际设备和支持的 RGB/深度流模式；未连接返回非零
bash scripts/camera.sh list

# 无硬件 LeRobot 插件测试
bash scripts/camera.sh test

# 相机完成 USB 接入后，保存 30 组真实 RGB-D 到一个新目录
bash scripts/camera.sh capture --config config/camera.gemini2.json --frames 30
```

VS Code“终端 → 运行任务”也提供 SDK 检查、枚举和采集入口。`check/list/capture` 不导入
机械臂控制模块，不创建 CAN 控制器，不发送电机命令。软件报告保存到
`artifacts/camera-check.json`、`camera-list.json`、`camera-capture.json`。

`config/camera.gemini2.json` 的 640×480、30 Hz、RGB 是初始请求模式，必须与 `list` 输出
核对；不支持时明确失败，不自动更换分辨率或降帧率。支持 MJPG 等格式时可显式修改
`color_format`。仅有一台匹配相机时可用 `serial_number=null`，多台必须填真实序列号。
深度选择同帧率的实际 Y16 模式，软件 D2C 后输出与 RGB 同尺寸。

采集保存到 `data/raw/gemini2-<唯一编号>/`：PNG 彩色图、uint16 NPY 深度、逐帧内参/深度
比例 JSON、含设备/主机时间戳的 `frames.jsonl`、最后一帧的 `scene.json` 和采集报告。
默认去畸变，深度重映射采用最近邻，保留无效深度为零。
相机到基座外参始终初始为 `T_base_camera=null`，不能直接用于抓取定位；现场测量后
另存标定版本，再使用已完成标定的文件回放。不会自动套用演示外参。

## 首次接入顺序

1. 用 USB 3 数据线把 Gemini 2 接到 Windows，打开上面已准备的 OrbbecViewer.exe。
   换机时从官方 [SDK v2 发布页](https://github.com/orbbec/OrbbecSDK_v2/releases)
   下载，或执行 `scripts/prepare_orbbec_viewer.ps1`。
2. 在设备信息中记录型号、序列号、固件与 USB 连接速率。打开 Color 和 Depth，使用设备
   实际支持的流模式，确认目标茶壶和桌面在视野内，连续预览不掉线。
3. 打开 D2C（深度对齐彩色）与设备支持的帧同步，观察物体轮廓是否对齐，记录 RGB/深度
   时间戳、实际分辨率和帧率。首轮建议持续记录至少一分钟作为采集稳定性检查。
4. 保存实际流模式的内参和畸变、深度比例及对齐/同步配置。深度比例必须从 SDK 读取；
   不能假设任意固件/模式都固定为 1 mm。相机内参也不能沿用演示 JSON。
5. 关闭 Windows Viewer 后，再按微软 USB 指南将相机独占附加到 WSL：确认 `usbipd list`
   中的实际 BUSID，完成绑定/附加，在 WSL 用 `lsusb` 检查。相机和 USB-CAN 各自确认，
   不复用猜测的 BUSID。本机 Linux udev 权限已设置；换机运行
   `sudo bash scripts/configure_camera_permissions.sh`。
6. 在 WSL 再验证 RGB 与深度采流、同步和持续运行。WSL 的 USB/驱动链路需要实测；
   若持续采集不稳定，可把采集与机械臂控制放在原生 Ubuntu，继续由 VS Code 远程开发。
7. 固定相机后测量相机到基座的外参 `T_base_camera`、TCP 与桌面几何，并用独立距离测量
   核对三维定位。SDK 的 RGB/深度外参不等于相机到机械臂基座的外参。

Windows Viewer 可以先单独验证相机，不需要连接或移动机械臂。相机开始输出图像后，
仍须完成机械臂基础验收及同步示教输入，才能录制动作学习数据。

## 如何用于当前训练

首版 ACT 的输入保持 RGB 图像 `observation.images.scene` 与七轴 `observation.state`，
输出为 `action` 七轴目标。Gemini 2 提供 RGB 图像，不会自动提供成功抓取演示。
需要通过已经验证的 Leader 或其他示教输入，完整录制抓起、抬升、放回的 episode。

RGB-D 另外保存深度、设备时间戳、主机采集时刻、比例和标定版本，用于三维定位与排错。
当前 ACT 配置没有深度输入；若后续增加深度，需同步修改数据字段、模型和部署处理器。
调整图像大小/裁剪时要记录映射，几何投影使用对应图像的实际内参。

LeRobot 相机适配器已经实现。先用以下无运动命令检查官方录制入口能加载项目插件：

```bash
bash scripts/camera.sh lerobot record --help
```

现场录制时，将已经核对的机械臂、Leader 和数据集参数传给
`bash scripts/camera.sh lerobot record`，相机参数片段如下：

```text
--robot.cameras='{"scene":{"type":"orbbec_gemini2","width":640,"height":480,"fps":30,"color_format":"RGB","use_depth":false}}'
```

分辨率/格式仍须先通过设备枚举和采集测试确认。该包装入口会先注册配置，再调用固定
LeRobot 的官方 CLI；普通 `lerobot-record` 不会自动发现本项目模块。
`record` 和 `teleoperate` 是真实设备入口，须在机械臂/主臂完成现场验收后使用。

后台采集支持 `read/async_read/read_latest` 和配对的 `read_latest_depth`；发现超时、
重复/倒序设备时间戳、RGB/深度不同步或通信错误后锁定错误，禁止用旧帧掩盖故障。
RGB 和紧接着读取的深度来自同一 packet，即使后台期间收到新帧也不会串帧。
首版录制保持 `use_depth=false`；深度通过上述 RGB-D 采集入口单独保存，未将其作为
ACT 视频输入或宣称已验证深度策略训练。
独立 RGB-D capture 与 LeRobot 录制分别占用相机，按顺序运行；当前没有在 RGB 示教录制
期间额外开启第二进程采深度。需要与动作共同保存深度时应在同一采集进程扩展数据契约。

本轮完整软件验收与ACT回归结果见
[相机准备验收摘要](reports/camera-preparation-2026-10-15.json)。

真实训练操作见 [训练入门](training-start.md)，原始数据规范见
[数据采集与备份](data-collection.md)。

## 官方依据

- [SDK v2 型号、固件和系统支持表](https://github.com/orbbec/OrbbecSDK_v2#14-supported-devices)
- [官方 Python SDK v2 分支](https://github.com/orbbec/pyorbbecsdk/tree/v2-main)
- [Orbbec Viewer：RGB、深度、D2C 与同步](https://github.com/orbbec/OrbbecSDK_v2/blob/main/docs/tutorial/orbbecviewer.md)
- [Microsoft WSL USB 接入](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
