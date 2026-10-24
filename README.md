# reBot Tea：B601-RS 视觉抓取项目

日常开发请打开仓库根目录的 `reBot-B601-RS.code-workspace`。`reBot-Codex-WSL` 是指向
同一仓库的旧 VS Code/WSL 快捷入口，并不是另一个项目。目录关系和文件用途见
[项目入口与文件导航](docs/project-navigation.md)。

环境和插件复核入口：`bash scripts/audit_software.sh`。本机已补齐 Notebook 内核、
Gemini 2 USB 权限和 Windows OrbbecViewer，结果与使用方法见
[软件环境检查](docs/software-environment-check.md)。

当前阶段目标是完成空茶壶抓放：抓稳、抬升、放回指定区域。
无实物时仍可开发的内容与实际缺口见 [抓取准备检查](docs/grasp-readiness.md)。
后续目标保留为定位随机摆放的茶杯及按反馈倒茶，本阶段暂停扩展。
当前两台相机分别为腕部 Gemini 305、第三视角 Gemini 2；茶杯不透明，目标为容量的90%。
两台相机已分别在 WSL 完成 RGB-D 短采集及约 10 秒双机同步运行检查；长时采集与标定尚未验收，
录制见 [操作说明](docs/dual-camera-recording.md)。
相机 SDK、RGB-D 采集和 LeRobot 插件已完成软件接入；使用 `bash scripts/camera.sh check`。
Windows 已枚举到本机 USB-CAN 为 candleLight (`1d50:606f`)；WSL 的 `can0` 和七电机通信已实测，见
[查询记录](docs/usb-can-identification.md)。
当前可运行功能与任务差距见 [任务设计](docs/tea-task.md)。

已建立无硬件开发环境：图片检测、RGB-D 回放、坐标转换、模拟抓取与独立运动学验证。
所有抓取执行均为 mock，不连接机械臂；图片检测使用 TorchVision COCO 预训练基线。
完整的合成抓放事件流程与故障锁存见 [合成空壶抓放状态机](docs/grasp-cycle-synthetic.md)；
它需要独立的抓持与释放确认证据，不代表实物抓放成功。
本次选定开发环境：Windows VS Code + WSL2 Ubuntu 22.04 + Python 3.10。
项目优先放在 Linux 文件系统的 `~/projects/rebot-tea`。

当前工作重点是抓放流程、运动学与受限执行衔接，暂缓杯子与液位功能。
首次实物接入请先读 [电脑连接与控制](docs/robot-first-connection.md)，
当前 Arm102 主从跟随的校准状态、短时启动命令及停止注意事项见
[Arm102 → B601-RS 现场步骤](docs/arm102-wsl-next-steps.md)。
切换到原生 Linux 前的项目、数据、训练输出和校准文件备份与恢复步骤见
[原生 Linux 迁移交接](docs/native-linux-migration.md)。
实物验收后按 [第一次训练](docs/training-start.md) 准备示教数据。

实物前的软件链路已补齐：受限关节模拟 → 原生 LeRobot 合成数据 → ACT 100步 GPU训练
→ 策略与预处理器重载。详见 [实物前开发与验收](docs/prehardware-development.md)。
运行 `bash scripts/verify_all.sh --with-training` 完整检查，报告写入
`artifacts/prehardware-report.json`。合成训练不代表已学会抓壶或倒茶。
现场记录、标注/备份和任务验收分别见
[接入记录](docs/commissioning-record.md)、[数据规范](docs/data-collection.md)、
[任务验收](docs/task-acceptance.md)。
实物到位后的完整顺序与每步通过条件见 [实物工作流程](docs/after-hardware-workflow.md)。
上一轮实测的软件结果见 [可随仓库同步的验收摘要](docs/reports/prehardware-2026-10-14.json)。
本次同步前的完整验收通过108项不同测试及ACT合成100步/重载，见
[2026-10-15完整验收摘要](docs/reports/github-sync-2026-10-15.json)。
GitHub 开源控制项目的筛选、已接入的 LeRobot 真机适配层及现场门禁见
[开源控制接入](docs/open-source-control-integration.md)。

墨将 USB 手柄的 Windows → WSL 输入桥、关节练习和 LeRobot 本地记录已接入，
按 [手柄遥操作教程](docs/gamepad-teleoperation.md) 先练模拟后端；
[合成双视角记录](docs/dual-camera-recording.md#6-如果使用手柄示教) 可检查 scene/wrist 数据字段与主机时间差，
尚未接入实机双相机手柄录制；
[相关 GitHub 项目比较](docs/gamepad-open-source-review.md) 说明接口、型号与验证范围。

## 开始使用

在 PowerShell 打开 Linux：

```powershell
wsl -d Ubuntu-22.04
```

在 Ubuntu 项目根目录执行：

```bash
bash scripts/bootstrap.sh
source .venv/bin/activate
python scripts/check_environment.py
python -m unittest discover -s tests -v
python -m rebot_tea --config config/simulation.json
python -m rebot_tea.replay
python scripts/replay_pouring.py
code .
```

VS Code 左下角应显示 `WSL: Ubuntu-22.04`，Python 解释器选择
`.venv/bin/python`。在此远程窗口中安装推荐的 Python、Pylance 和 Debugger 扩展。
按 F5 选择“模拟抓取（无硬件）”或“离线 RGB-D 抓取回放”。
运行任务“完整离线验收”检查环境、代码、测试、模型推理与合成训练步骤。
仅将 Windows 终端切换为 bash 不等于连接 WSL，代码必须由 Linux Python 执行。

## 当前架构

```text
fixtures/detection.json（合成 base 坐标，代替视觉输入）
        ↓ perception.py：统一 Detection
        ↓ planning.py：置信度、坐标系、有限数值检查
        ↓ 接近 / 下降 / 夹持 / 抬升的任务步骤，整组检查示例边界
        ↓ robot.py：MockRobot 记录动作
        ↓ __main__.py：打印模拟结果

RGB 图 / 录制 RGB-D
        ↓ vision.py：SSDLite COCO 检测 / 显式合成检测
        ↓ geometry.py：已对齐深度 + 内参反投影 + T_base_camera
        ↓ replay.py → planning.py → MockRobot → artifacts/replay.json
```

`config/simulation.json` 是模拟配置；单位为米，坐标系为 base。
配置中的工作区边界只用于演示测试，**不代表 B601-RS 的真实安全工作区**。
目标点也只是合成测试值，不是物体中心到夹爪 TCP 的真实换算。
该阶段没有抓取姿态、IK、关节轨迹、碰撞检查、接触反馈或抓取成功判断。
MockRobot 不控制夹爪。关节层新增了 LeRobot B601-RS 适配器，但只有完整的现场配置通过
门禁后才接受硬件后端；当前模板保持禁用，笛卡尔抓取流程仍只使用 mock。

## 接入真实抓取的顺序

1. **硬件与基础通信**：确认 USB-CAN 型号/驱动、实际接口名、固件和电机 ID。
   按 Seeed B601-RS 文档完成机械装配、零位标定与低速单关节验证。
   不直接沿用 DM 型号参数；不能用“读取位置”函数名判断接口不会使能电机。
2. **相机与标定**：使用第三视角 Gemini 2、腕部 Gemini 305 两台 Orbbec RGB-D 相机。
   实现相机采集，记录内参/畸变、深度单位、时间戳和外参。
   RGB-D 像素先反投影到相机坐标，再变换至 base；纯 RGB 必须增加已标定桌面
   或其他深度约束。二维检测框本身不能提供可靠的三维抓取位置。
3. **机器学习感知**：先以固定物体检测/分割模型作为基线，验证杯/壶的检测效果。
   确认相机和算力后再选模型及 PyTorch/CUDA 版本；不要现在安装任意 CUDA 组合。
   将模型结果转换为统一 Detection，并扩展为含姿态、TCP 偏移的抓取目标。
4. **运动学与驱动**：使用对应 RS 机械结构的 URDF、关节方向/零偏和工具坐标；
   本项目已独立验证官方 RS URDF、网格和 FK；具体结果见 docs/kinematics.md。
   实际规划链还需接入抓取姿态、工具偏移、轨迹和碰撞检查。
   再实现 SDK 适配层、反馈到位等待、超时、夹持检测与现场停止策略。
5. **真实抓取验收**：低速完成空夹爪、空杯抓取及抬升，记录重复成功率。
   抓取稳定后扩展随机杯定位与液位反馈倒茶；另需液体、负载和倾倒轨迹验证。

相机已在 WSL 分别短采集，`can0`/`gs_usb` 的 1 Mbps 只读探测确认七电机在线；
双臂零位和 RS 参数已完成本次校准、回读；运动、停止行为及断电后参数持久化仍待现场验收。
视觉暂用 SSDLite 预训练杯子检测基线。
该 COCO 模型没有 teapot/kettle 类别，不能直接识别茶壶，也没有液位估计能力。
不将示例 CAN 参数和示例坐标写入真实驱动。
WSL 设备接入必须在选定适配器后验证；若驱动/USB 透传不适配，可将本 Python
项目迁移至原生 Ubuntu 或通过 Remote-SSH 连接 Ubuntu 控制机。

## 文件说明

- `pyproject.toml`：Python 包与命令入口，核心流程无第三方运行依赖。
- `.vscode/`：Linux 解释器、F5 调试、测试与环境检查任务。
- `config/simulation.json`：明确隔离模拟模式。
- `src/rebot_tea/`：感知、任务规划、机器人适配与流程入口。
- `scripts/check_environment.py`：只读环境诊断；可选库缺失不判定核心模拟失败。
- `tests/test_grasp.py`：流程顺序、异常输入、抬升越界、禁用真实后端等验证。
- `docs/environment-report.md`：本次检查结果与限制。
- `docs/offline-development.md`：图片检测、回放输入格式和验收命令。
- `docs/kinematics.md`：独立 Pinocchio 环境与官方 RS 模型验证。
- `docs/hardware-preparation.md`：设备、标定数据和真机接入准备清单。
- `docs/tea-task.md`：茶壶识别、随机杯定位与90%容量反馈的任务设计及待实现部分。
- `docs/github-sync.md`：仓库位置、同步范围和后续提交方法。
- `docs/robot-first-connection.md`：设备自检、探测、官方控制界面与初次校准。
- `docs/training-start.md`：主臂示教、数据采集、ACT短训练与后续评估。
- `docs/open-source-control-integration.md`：GitHub 项目比较、LeRobot 适配和真机命令门禁。

## 官方参考

- [VS Code WSL 开发](https://code.visualstudio.com/docs/remote/wsl)
- [Seeed B601-RS 快速入门](https://wiki.seeedstudio.com/rebot_b601_rs_getting_started/)
- [Seeed B601-RS 电机 SDK](https://wiki.seeedstudio.com/cn/rebot_arm_b601_rs_mit_control/)
- [Seeed B601-RS Pinocchio](https://wiki.seeedstudio.com/rebot_arm_b601_rs_pinocchio_meshcat/)
- [Seeed reBot-DevArm](https://github.com/Seeed-Projects/reBot-DevArm)
