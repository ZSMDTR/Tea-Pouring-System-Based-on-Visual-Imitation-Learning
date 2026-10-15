# 环境、软件与插件复核（2026-10-15）

用户已确认配件均为原厂套件。工程按官方 PCAN-USB 路线准备，配置已记录来源；
设备的 VID/PID、序列号、固件、关节校准与限位仍待实物读取，不用来源确认代替通信验收。

## 日常从这里检查

打开 `/home/zsmdtr/projects/rebot-tea/reBot-B601-RS.code-workspace`，在 WSL 终端运行：

```bash
cd ~/projects/rebot-tea
bash scripts/audit_software.sh
```

也可选择 VS Code“终端 → 运行任务 → 环境与插件检查（不连接机械臂）”。
该入口检查三个环境的依赖与锁定版本、上游提交、CUDA、原生 SDK、两个 Notebook 内核、
内核驱动模块、相机权限及 Windows/WSL 扩展。Windows 部分只读取安装、服务与设备列表，
加载 PCAN-Basic DLL 后立即释放；不会初始化 CAN、绑定 USB、启动网关或发送电机命令。
软件检查缺项时返回非零；没有相机或 CAN 不会被伪报为设备通过，也不会导致软件检查失败。

报告：`artifacts/software-audit.json`；Windows 原始清单：`artifacts/windows-software-audit.json`；
Notebook 执行结果：`artifacts/notebook-check.json`。原始清单只留本地，不随 Git 上传。
可随仓库保存的摘要见 `docs/reports/software-environment-2026-10-15.json`。

## 已检查的软件

| 项目 | 本机状态与运行环境 |
|---|---|
| Windows / WSL | Windows 11，WSL2 Ubuntu 22.04.5，内核 6.18.33.2；systemd/udev 运行 |
| 主环境 | `.venv`，Python 3.10.12，视觉、MotorBridge、调试与普通测试 |
| 训练环境 | `.venv-training`，Python 3.12.15，固定提交 LeRobot、ACT、PyAV/FFmpeg、TorchCodec |
| 运动学环境 | `.venv-kinematics`，Pinocchio 3.9.0，与另外两个环境分离 |
| GPU | RTX 5060 Laptop；主环境和训练内核均实际执行 CUDA 运算 |
| PyTorch / TorchVision | 2.10.0+cu128 / 0.25.0+cu128；不需另装系统 CUDA Toolkit 来运行这套 wheel |
| Gemini 2 SDK | 两个环境均为 `pyorbbecsdk2==2.0.18`，原生配置和对齐 API 可加载 |
| Linux USB/CAN | `can/can_raw/peak_usb/gs_usb/vhci_hcd/uvcvideo` 模块文件可用，尚无实物驱动绑定结果 |
| 相机权限 | 已安装 SDK 自带的 `99-obsensor-libusb.rules` 并重载规则，接入时生效 |
| Windows USB 透传 | usbipd-win 5.3.0，服务运行；尚未绑定本项目设备 |
| Windows PCAN | PEAK-Drivers 5.1.3.1189；PCANBasic.dll x64/x86 5.1.0.1194，x64 实际加载通过 |
| Windows 相机查看器 | 官方便携 OrbbecViewer 2.7.6 已解压到 `D:\reBot-tools\OrbbecViewer-v2.7.6\OrbbecViewer.exe`，未启动实物预览 |
| VS Code Windows 端 | Remote-WSL 扩展已安装；客户端更新后下次正常重新连接即可刷新远程服务，无需现在强制重启 |
| VS Code WSL 端 | Python、Pylance、debugpy、Ruff、Jupyter 扩展已安装；另有 CSV、Data Wrangler、C++/CMake 等辅助扩展 |

本轮补齐了两个环境的 `ipykernel/nbformat`，实际启动两个内核执行 CUDA 和相机 SDK 检查。
新增依赖的实际版本已写入各自锁定文件。脚本清理继承的 `PYTHONPATH/PYTHONHOME`，
避免系统 ROS 路径混进工程环境；保留系统 ROS 和已有编辑器的 ROS 配置。

## 使用 Notebook

在 VS Code 打开或新建 `.ipynb`，右上角“选择内核”选择：

- `reBot Tea - Python 3.10`：视觉分析与项目普通代码。
- `reBot ACT - Python 3.12 CUDA`：LeRobot 数据检查与训练实验。

两个内核都指向项目内绝对解释器路径。训练内核另设 FFmpeg 库目录，不依赖终端先激活
Conda。正式的可重复训练仍从 `bash scripts/training.sh ...` 或固定版本 CLI 启动。
换机或移动仓库后，在三个工程环境完成初始化后运行：

```bash
bash scripts/bootstrap_notebooks.sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python scripts/check_notebooks.py
```

相机 Linux 权限在新系统中需要系统配置权限：

```bash
sudo bash scripts/configure_camera_permissions.sh
```

本机已完成，不必重复。脚本对不同的旧规则先备份，只重载权限规则，不刷固件。
换 Windows 机器时可在 PowerShell 执行 `scripts/prepare_orbbec_viewer.ps1`，下载固定官方
版本并核对本轮记录的 SHA-256；默认目标目录已有文件时拒绝覆盖。
下载来源：[官方 v2.7.6 发布页](https://github.com/orbbec/OrbbecSDK_v2/releases/tag/v2.7.6)。

## 完整软件链路与实物待办

依赖变更后使用 `bash scripts/verify_all.sh --with-training` 检查离线控制、数据质量、
视觉/运动学、合成 ACT 100 步及检查点重载。结果与环境检查摘要一起记录。
这些命令不会证明机械臂已经能够抓取。

接下来需要实物完成：Windows Viewer 的 RGB/深度预览与固件读取；核对两个 USB 设备的
BUSID 后按 [微软指南](https://learn.microsoft.com/en-us/windows/wsl/connect-usb) 绑定/附加；
检查 WSL 实际流模式与稳定性；CAN 只读反馈、零位、方向、实际限位、TCP、停止行为及
手眼标定。具体顺序见 [首次机械臂连接](robot-first-connection.md) 和
[Gemini 2 接入](camera-gemini2.md)。

Docker、CVAT/Label Studio、DVC、TensorBoard、ROS 控制栈和远程 Ubuntu 主机都不是当前
合成 ACT 链路的前置条件。需要真实图片标注、团队数据版本或改用 ROS 规划时再选型，
不为“可能用到”安装多套重叠环境。当前有 Git、原生数据检查和 SHA-256 备份入口。
