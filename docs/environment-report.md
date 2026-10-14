# 环境检查报告

检查日期：2026-10-14。

## 已确认

- 提供的 Windows 工作区初始为空，因此没有旧配置或旧业务代码需要修复。
- 已安装 WSL2，默认发行版 Ubuntu-22.04 正在运行；另有 Ubuntu-24.04。
- Ubuntu-22.04 实际系统版本为 Ubuntu 22.04.5 LTS。
- Linux 用户为 zsmdtr，Python 为 3.10.12，Git 和 pip 可用。
- Linux 系统 Python 可找到 cv2 与 numpy，未找到 torch、motorbridge、pinocchio。
  系统 Python 的包不等于独立虚拟环境内已安装的包。
- /opt/ros/humble 目录存在；这不代表 ROS 节点、DDS 或运动控制已验证。
- Windows VS Code 已安装 WSL、Remote-SSH、Python 和 Debugger 扩展；
  WSL 远程端扩展需单独确认。
- Windows Python 3.13.13 下 5 项单元测试通过，模拟抓取步骤输出正确。
- Windows 环境检查按预期报告非 Linux、未处于虚拟环境；不能用此结果推断 WSL 缺失。
- 已在 /home/zsmdtr/projects/rebot-tea 创建项目，创建 .venv 并成功以 editable 方式安装。
- WSL 内环境检查全部 5 项必需条件通过，5 项单元测试通过，完整模拟流程通过。
- 独立 .venv 已安装并验证 OpenCV、NumPy、PyTorch、TorchVision、MotorBridge 和调试工具；
  Pinocchio 位于单独 .venv-kinematics，ROS 保留在系统环境。
- WSL 中没有检测到 CAN 接口；这不构成对未知 USB-CAN 型号的兼容性结论。

## 决策

复用 WSL2 Ubuntu 22.04，在 ~/projects/rebot-tea 中开发。
先采用无 ROS 依赖的 Python 核心，便于在硬件未到位时验证流程。
根据已检测的 RTX5060 Laptop GPU 安装 CUDA12.8 对应 PyTorch，使用轻量预训练模型；
运动学以独立环境加载固定提交的官方 RS 模型。未安装大型物理仿真或VLA训练栈。

## 尚未验证

相机与 USB-CAN 型号/连接、Linux CAN 驱动与波特率、机械臂电机 ID、零位、
真实 TCP/手眼标定、实际场景检测效果、全局可达性、碰撞检查及真实抓取。

WSL 启动输出出现 localhost 代理/NAT 提示；本次 pip 安装已成功，
未修改用户的 Windows 或 WSL 全局代理配置。CUDA依赖与官方模型权重已下载成功；
最初官方 wheel 源速度较慢，改用 PyPI 后完成同版本安装。

## 本轮完整离线验收

2026-10-14 在 Linux 主项目执行完整验收，退出码为0。

| 项目 | 结果 |
|---|---|
| Python / WSL | Python3.10.12，Ubuntu22.04.5，WSL2 |
| GPU | NVIDIA GeForce RTX5060 Laptop GPU，8151MiB显存，驱动573.24 |
| PyTorch / TorchVision | 2.10.0+cu128 / 0.25.0+cu128 |
| OpenCV / NumPy | 4.12.0 / 2.2.6 |
| MotorBridge | 0.5.6，导入成功；未创建电机连接 |
| Debugger / Ruff | debugpy1.8.22 / Ruff0.17.0，静态检查通过 |
| 依赖完整性 | 两套虚拟环境 pip check 均通过 |
| 回归测试 | 16项全部通过 |
| RGB-D回放 | 合成像素/深度经内参和外参得到base目标，5个mock步骤通过 |
| 真实模型CPU/GPU推理 | SSDLite COCO权重加载与前向推理通过 |
| GPU训练冒烟检查 | 合成输入1次前向、反向和优化器更新通过，未保存训练模型 |
| 图片命令输出 | 合成图输出检测JSON和标注PNG，0个cup，未伪造检测 |
| Pinocchio | 3.9.0，正确RS URDF和25/10个可视/碰撞几何可加载 |
| FK/Jacobian | 有限值、旋转正交、差分核对通过，最大雅可比误差2.37e-8 |
| IK回环 | 邻近初值25次收敛，位置误差1.43e-9m，所有迭代在URDF限位内 |
| ROS Humble | source系统setup后，系统Python可导入rclpy；未启动ROS控制链 |
| VS Code | Linux侧Python/Pylance/debugpy扩展存在，调试/任务JSON格式通过 |

模型：官方TorchVision SSDLite MobileNetV3 COCO权重，已校验文件名哈希并缓存models/。
上游机械模型固定提交：6415d43130d1e143c70dc106096a857ac5556f81。
版本锁定：requirements/lock-linux-cu128.txt、requirements/kinematics.txt。
计算报告：artifacts/vision-cpu.json、vision-cuda.json、kinematics-check.json。
回放产物：artifacts/replay.json、waypoints.png、demo-detections.json及PNG。

以上验证仅证明软件环境和离线计算可运行。合成训练不是有效任务训练，
FK生成目标的局部IK回环不是通用抓取规划，碰撞几何加载也不证明路径无碰撞。
真实数据准备与设备验收清单见 docs/hardware-preparation.md。

## 90%容量倒茶反馈扩展

用户进一步确认：机械臂完整套件和相机已准备，不透明茶杯，目标为容量90%，相机接入暂缓。
新增纯离线倒茶控制器和合成回放，包含每帧抓持及壶嘴对准确认、容量阈值、杯身份/位置、
观测时效、时间顺序、置信度和超时检查；达到目标或异常时请求停止并立壶，终态不自动恢复。
新增18项测试后，全项目34项测试通过，Ruff、配置格式和回放检查通过。
容量、抓持、对准、物理停止与余流仍未经过实物验证；现有COCO模型不含茶壶类别。
GitHub目标为ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning，保留私有可见性。
