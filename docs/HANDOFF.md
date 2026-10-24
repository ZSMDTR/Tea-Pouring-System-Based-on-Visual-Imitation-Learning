# 项目交接：Seeed reBot Arm B601-RS 茶壶抓取与倒茶

更新时间：2026-10-24

WSL 项目路径：`/home/zsmdtr/projects/rebot-tea`  
GitHub 仓库：`https://github.com/ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning`

> **新对话开始时先检查当前工作区文件。** 先进入上述 WSL 项目，阅读本文件、`README.md` 和 `docs/robot-first-connection.md`，然后运行 `git status --short --branch`。以当前文件和代码为准，不要假设此前状态仍未变化，也不要先重建或覆盖已有环境。

## 2026-10-23 设备身份与双相机录制更新（优先于下方历史状态）

- 用户确认腕部相机为 **Orbbec Gemini 305**、第三视角为 **Orbbec Gemini 2**。
  Windows 枚举分别为 `2bc5:0840`、`2bc5:0670`。2026-10-23 已通过 `usbipd`
  附加到 WSL，`lsusb` 与 Orbbec SDK 同时识别两台；各自 5 帧 RGB-D 短采集成功。
  Gemini 305 固件 1.0.70、USB 2.1；Gemini 2 固件 1.4.92、USB 3.0。
  尚未验证双机同时长时采集、外参或真实示教录制。重启/拔线后需重查附加状态。
- Windows 只读枚举 USB-CAN 为 **candleLight `1d50:606f`**；早期 PCAN 结论是设备
  未枚举前的资料推断。2026-10-23 已将该适配器附加到 WSL，`gs_usb` 自动创建
  `can0`。用户确认机械臂已固定、接好 CAN/电源并上电后，已把 `can0` 配置为
  1 Mbps；`scripts/control_linux.sh doctor can0` 通过。只读探测确认 1–7 号电机
  全部在线且位置读数有效，结果在 `artifacts/motors-linux.json`。探测只发查询帧，
  未使能、校零、写参数或运动。零位、参数回读、关节方向/限位和停止行为仍未验收。
- 同日已运行 `bash scripts/control_linux.sh gateway can0`，网关监听
  `ws://127.0.0.1:9002`；Windows 本机 TCP 端口检查通过，并已打开 MotorBridge Studio。
  这只证明软件入口可达，Studio 内连接、型号选择、扫描及任何实物调试仍需现场确认。
  当前会话结束、WSL 重启或网关停止后，须重新检查并启动服务。
- 双相机 LeRobot 插件 `orbbec_gemini305`/`orbbec_gemini2` 对应 `wrist`/`scene`，
  配置见 `config/cameras.teapot-dual.json`，操作见 `docs/dual-camera-recording.md`。
  640×480 RGB 30 fps 是待相机列表确认的请求值，非实机验收结果。
- 标准 `lerobot-record` 需真实 teleop。**Arm102 主臂是否具备仍未知**；文档中的
  `rebot_102_leader` 命令仅在有主臂且已完成实物验收时使用。现有手柄记录入口只录
  单台相机，不能与双相机数据字段或训练配置直接混用。
- 本轮离线测试验证插件注册、双相机配置解析和原生 LeRobotDataset 双图像字段；
  两台相机分别完成短采集，示教、真实数据录制和模型训练仍未执行。

## 2026-10-24 WSL → 原生 Linux 迁移交接（当前优先）

- 当前可访问的工程是 WSL2，不是原生 Linux：`/home/zsmdtr/projects/rebot-tea`；内核为
  `6.18.33.2-microsoft-standard-WSL2`，Python 3.10.12。请勿把本次云/终端会话误认为
  已在原生 Linux 中完成安装或硬件验收。
- GitHub `main` 与本地 `main` 当前都在 `7ec8527`，本地有已暂存、未暂存和未跟踪改动。
  双相机、游戏手柄及 Arm102 交接文件均有未推送内容。迁移以完整项目备份为准，
  不能只从 GitHub 克隆。详见 [原生 Linux 迁移包与步骤](native-linux-migration.md)。
- 已确认示教主臂是 reBot Arm102。`leader.json` 与 `follower.json` 均已保存；本轮不重做校准。
  最近两次主从启动都被首次姿态差保护拦截，未通过第一条动作目标。最新截图读数为：
  shoulder_pan 主臂 -16.2° / 从臂 -2.1°，wrist_flex -6.3° / +0.1°，
  wrist_roll +19.4° / +1.2°。继续保持 5° 检查；先对齐姿态，再在现场逐轴验证方向和停止。
- 当前 WSL 会话的 USB-CAN、CH340、两台 Orbbec 设备未附加；`ip -br link` 只有 `lo`、`eth0`，
  没有 `can0`。这只描述本次 WSL 会话。进入原生 Linux 后应重新检查 USB、CAN 和串口。
- 当前 WSL 的 `nvidia-smi` 可识别 RTX 5060 Laptop，驱动 573.24，显示 CUDA 12.8；
  原生 Linux 仍须独立安装/确认 NVIDIA 驱动并运行 `nvidia-smi`。
- 备份包不含 `.venv*`；新系统要在核实 GPU 驱动后重建。仓库没有单独安装系统 CUDA Toolkit 的脚本；
  `bootstrap.sh` / `bootstrap_training.sh` 安装锁定的 PyTorch CUDA 12.8 wheels 与项目环境。

## 1. 项目目标和硬件型号（历史基线；与上方当前状态冲突时以上方为准）

最终任务：视觉识别茶壶并抓取，向随机摆放的不透明茶杯倒水；达到杯容量的 90% 时停止。用户已说明机械臂整套和相机已具备。目前阶段聚焦电脑连接及机械臂控制，杯子和相机暂缓。

- 机械臂：Seeed Studio reBot Arm B601-RS。
- 相机：Orbbec Gemini 2（2026-10-15 用户确认）；固件、安装方式及标定待验证。
- USB-CAN：官方套件使用 PCAN-USB 兼容接口；实际板卡品牌/VID/PID 尚未核对或接入。
- 示教主臂/Leader：是否有及型号未知。
- 零位校准、参数写入和电机在线状态：未确认。

## 2. 当前开发环境

主开发环境为 Windows 上的 WSL2，发行版 Ubuntu-22.04；在 WSL 中打开 VS Code Remote WSL，使用仓库的 Linux 环境。

- `.venv`：主环境，Python 3.10；MotorBridge 0.5.6、PyTorch 2.10.0+cu128、torchvision 0.25.0+cu128、OpenCV 4.12。
- `.venv-kinematics`：独立运动学环境，Pinocchio 3.9.0。
- `.venv-training`：已建立独立 Conda 前缀环境，Python 3.12.15、LeRobot 0.6.2 固定提交；
  PyTorch 2.10.0/CUDA 12.8、TorchVision 0.25.0、TorchCodec 0.10.0、PyAV 15.1.0。
- 机器此前识别为 RTX 5060 Laptop，约 8 GB 显存；WSL 中有 ROS 2 Humble，但当前主流程未依赖 ROS。
- Windows 的 `.venv-control-win` 是另一套环境，不与 WSL 环境混用。
- 官方 Python SDK 源码在 `vendor/reBotArm_control_py`，固定上游提交 `6415d43130d1e143c70dc106096a857ac5556f81`。

## 3. 已完成的功能

- 建立离线 Python 项目、依赖环境、VS Code 配置和 MockRobot 流程。
- RGB-D 几何投影和离线视觉演示；当前 COCO SSDLite 模型不识别专用茶壶，也不估液位。
- 离线语义动作规划、抓取回放、倒水体积比例模拟；都不向实物发动作。
- B601-RS MotorBridge 只读连接探测：检查 1–7 号电机在线与新鲜位置。ID 1–3 配置 rs-06，4–7 配置 rs-00，主机 ID 0xFD。
- Linux/Windows 连接诊断脚本、CAN 配置入口和官方 MotorBridge gateway 启动入口。
- URDF 运动学 FK/Jacobian 数值检查及 IK 往返演示。
- 七轴公共坐标受限轨迹与 mock 故障处理，原生 LeRobot 合成数据检查，
  ACT 100步GPU训练、检查点/处理器重载，以及现场记录和验收工具。

**未实现真实机械臂动作控制、真实抓取、茶壶任务训练或实物水量反馈。**

## 4. 修改过的重要文件

- `src/rebot_tea/connection.py`：只读 MotorBridge 连接/电机状态查询。
- `scripts/doctor_control.py`：无 CAN 帧的环境、USB/CAN 诊断。
- `src/rebot_tea/{robot,planning,replay,pouring,vision,geometry}.py`：模拟机器人、离线规划/回放、视觉和几何。
- `scripts/control_linux.sh`、`scripts/control_windows.ps1`：Linux/Windows 诊断、probe、gateway 和接口命令。
- `scripts/doctor_control.py`、`scripts/check_environment.py`、`scripts/check_kinematics.py`、`scripts/verify_all.sh`：诊断与离线检查。
- `.vscode/tasks.json`、`.vscode/launch.json`：常用 VS Code 任务和离线启动项。
- `requirements/`、`pyproject.toml`：项目依赖及锁定文件。
- `docs/robot-first-connection.md`、`docs/training-start.md`、`docs/environment-report.md`：首次接臂、训练准备和环境记录。
- `docs/HANDOFF.md`：本交接文档。

## 5. 已通过的测试

以下是旧会话记录的通过结果；新会话实测结果见第 9 节：

- Linux：48 个单元测试、Ruff 检查通过。
- Windows：连接/诊断相关 14 个测试通过。
- Bash 脚本语法、PowerShell 脚本解析、SDK 导入、gateway 帮助检查通过。
- 运动学数值比较、CPU/GPU 视觉推理和离线回放通过。
- 临时虚拟 CAN `rebotvcan0` 上 SDK probe 流程可运行，但 7 个电机均离线；虚拟接口已移除。此项不代表实物连接成功。

## 6. 未完成的问题（2026-10-15 历史清单；当前事项见上方迁移交接）

1. USB-CAN 型号、驱动和由 Windows/WSL 哪一侧持有尚未确定；真实 CAN 接口和机械臂从未连通验证。
2. 1–7 号电机在线状态、固件、零位校准、参数读回及运动方向待现场确认。
3. 项目已具备限位、速度/加速度、到位反馈与超时的模拟控制层和接口，
   尚未接入或验证真实运动后端；示例参数不是实测安全参数。
4. IK 数学演示不验证真实可达性、碰撞、TCP 或负载；当前没有 SRDF 自碰撞配置。
5. 相机已确认 Orbbec Gemini 2；相机采集/标定、示教主臂与实际茶壶数据尚缺；
   LeRobot 环境和合成软件链路已准备。
6. 茶壶专用抓取策略未训练；不透明杯的实际反馈传感、余流测量和停流策略待现场验证。
7. Git 状态和远端应由新对话重新检查；交接文件是否已提交不能假定。

## 7. 原 WSL 工作顺序（历史记录；迁移后步骤见 native-linux-migration.md）

### P0：在 WSL 接手工程

```bash
cd ~/projects/rebot-tea
git status --short --branch
source .venv/bin/activate
code .
```

如状态与记录不同，先检查差异，保留用户改动。

### P1：识别 USB-CAN，建立只读连接

先从 Windows 设备管理器或 USB 设备信息获取适配器型号/硬件 ID；不要把蓝牙 COM 口当 CAN，也不要猜适配器。选 Windows 或 WSL 独占设备。走 WSL USB 直通时检查 usbipd-win、绑定/附加和 Linux 驱动；PEAK PCAN 需确认对应驱动或改评估 Windows SDK。

连接后在 WSL 检查：

```bash
lsusb
ip -details -br link
bash scripts/control_linux.sh doctor
```

确认真实 SocketCAN 接口名后（以下假设为 can0）：

```bash
bash scripts/control_linux.sh can-up can0
bash scripts/control_linux.sh doctor can0
bash scripts/control_linux.sh probe can0
```

目标是 CAN 接口 UP、1 Mbps，且 7 个电机在线并读到有效新位置。can-up 需要 sudo。若 ping 通但参数 0x7019 读取失败，查看固件和官方工具状态；不要自动使能、写零位或重置 ID。

### P2：官方校准及受限控制层

按 Seeed/MotorBridge 对应流程确认电机类型、ID、方向，完成零位和参数写入/读回。随后才开发真实控制：以实测状态为起点，经过确认的软限位和速度/加速度限制、到位/超时/异常处理、明确停止策略；从单关节小动作开始，再验证夹爪和短轨迹。操作时支撑机械臂并确保能立即断电。每次保存接线、参数和校准记录。

### P3：抓取数据和训练

先确认是否有兼容 Leader（例如 reBot Arm 102），再确认相机型号和固定安装方案。控制稳定后建立独立 LeRobot 环境，先录制空茶壶抓取/放置的成功及失败片段，检查并备份数据，再做小规模 ACT 训练和新位置回放评估。抓取稳定后再做倒水和 90% 水量反馈设计。

## 8. 重要路径、命令和限制

- WSL 仓库：`/home/zsmdtr/projects/rebot-tea`；Windows 访问路径：`\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea`。
- 首次接臂完整说明：`docs/robot-first-connection.md`；训练准备：`docs/training-start.md`；验收记录：`docs/environment-report.md`。
- 离线检查：`python -m unittest discover -s tests -v`、`ruff check src scripts tests`、`bash scripts/verify_all.sh`（最后一项会运行多项检查并写入本地 artifacts）。
- CAN 当前接口未知；只有确认真实设备后才用 `can-up` / `probe`。未接 CAN 时 doctor 报总线未就绪属于预期。
- SDK probe 是只读；gateway 可能提供真实控制界面，必须由操作者明确启动并现场监控。
- 零位/电机参数未确认前不发运动命令。Mock、模拟体积、URDF 数值结果和离线训练冒烟均不能当作实机验收。
- 同步 GitHub 需要显式 commit/push；本次仅更新 WSL 工作区文档，提交状态需后续用 Git 检查确认。

## 9. 新会话接续状态（2026-10-14）

- 已核对实际仓库 README、全部 docs、Git 工作区及测试。接手时 HEAD 为 `21d55aa`，
  已跟踪文件无差异，只有本交接文件未跟踪；当前会话启动目录不是代码仓库。
- 新发现终端继承的 ROS `PYTHONPATH` 干扰项目依赖检查，已修复
  `scripts/verify_all.sh` 的进程内隔离，并补充独立运动学环境的依赖检查。
- 修复后完整离线验收退出码 0，48 项测试、Ruff、两套环境依赖、CPU/GPU 推理、
  合成训练检查、回放及运动学全部通过。日志：`artifacts/offline-verification.log`。
- 已运行 Linux 只读 doctor：SDK 可用，无 CAN 接口。用户明确确认 **USB-CAN 尚未插入**。
  当前停留在 P1 的设备识别前，尚未执行实物探测、总线配置、校准或动作。
- 本会话 Windows 程序无法执行（`Exec format error`），Windows 检测结果仍为旧记录。
  远端查询因认证不可用而失败；本轮未 commit/push，本文件仍未跟踪。
- 后续先插入适配器并获取实际型号/硬件 ID，再选独占接入路径；详细复核记录见
  `docs/environment-report.md` 的“新会话接手复核”。

## 10. 实物前软件准备实施（2026-10-14）

第9节保留接手时的快照。当前软件工作已按用户批准的计划实施：

- 基线保存为 `2d3454c`；本轮功能在完整验收后另行提交并尝试普通推送。
- 七轴顺序、公共关节rad、五次轨迹和50Hz模拟控制已实现；故障终态不自动恢复，
  仅mock后端，无SDK动作适配。合成限值与未填写的现场模板分开保存。
- LeRobot固定提交 `ae2b57f12223c8c5184fcd814eb63cd9ce7de203`，训练环境已建立，
  两份训练依赖锁定文件和初始化/启动入口已提供。
- 原生合成数据5条×120帧、20Hz、224×224 RGB，公共deg，动作来自实际mock目标；
  episodes 0–3训练，episode 4验证。质量、损坏视频和字段/划分错误检查已实现。
- ACT默认结构、batch2、100步GPU训练、检查点及预/后处理器重载已通过；
  所有结果标记synthetic且不证明物理任务成功。数据与检查点未上传。
- 现场接入表、COCO茶壶/杯标注、示教检查/失败分类、SHA-256备份及任务CSV统计已准备。
  用户选择首版标准：空壶抓放20次至少18次成功，倒茶90%容量允许±5个百分点。
- 完整入口：`bash scripts/verify_all.sh --with-training`；
  主报告 `artifacts/prehardware-report.json`，日志 `artifacts/prehardware-verification.log`。
- 详细运行说明见 `docs/prehardware-development.md`；实物顺序与交接仍从P1设备识别继续。
  USB-CAN尚未插入，未执行总线配置、探测、校准、参数写入或实物运动。

本轮功能提交为 `0b716a5`，完整验收通过（74项核心测试及4项视频集成测试）。
已尝试普通 `git push origin main`，当前会话HTTPS认证不可用，Git报告无法读取用户名，
因此本地main仍有未推送提交；未强制推送、未改变远端仓库可见性。
后续在已有认证的终端执行普通推送即可，实际远端状态仍需查询确认。

## 11. 实物操作文档与结果同步（2026-10-14）

已补充 `docs/after-hardware-workflow.md`：从装配、USB-CAN、只读通信、校准、手动/程序控制，
到空壶抓放、相机/示教、数据、ACT、随机杯和水量验收，逐步写明通过条件。
步骤中的真实执行后端、policy rollout和水量反馈标明需要后续现场开发，未当作已实现。
本轮只整理文档与同步，未执行任何实物动作。

上一轮实测结果另存 `docs/reports/prehardware-2026-10-14.json`，可随Git上传；
74项核心测试、4项视频集成测试和ACT100步/重载通过，原始日志和检查点仍在本地。
本次文档更新不重跑训练，摘要明确保留原验收来源。

当前WSL无Git认证助手/登录凭据，直接启动Windows程序报Exec format error。
本轮发现可经WSL内部/init转发运行现有Windows Git，正在核对其缓存认证并尝试普通同步。
已验证该Windows Git/GCM缓存认证可读取远端main（查询时为21d55aa）；
本轮使用这条路线完成普通推送，保留既有历史和远端可见性。
推送的最终结果由实际Git输出及交付记录确认；操作方法见 `docs/github-sync.md`。

## 12. GitHub 开源控制项目接入（2026-10-15）

- 检索并比较了 Hugging Face LeRobot、Seeed B601 LeRobot 插件、MotorBridge、reBot-DevArm、
  Seeed ROS 2 控制器和 Web UI；版本、许可证与取舍记录在
  `docs/open-source-control-integration.md`。
- 选用项目已固定的 LeRobot 提交 `ae2b57f12223c8c5184fcd814eb63cd9ce7de203` 的
  `RebotB601Follower`。独立 Seeed 插件的 README 仍将 RS 标为完善中；ROS 2 仓库当前没有
  许可证文件，因此未复制其源码。
- 新增 `src/rebot_tea/hardware.py`：将官方驱动的公共度数转换为项目七轴弧度坐标，并接入
  现有五次轨迹、限位、速度/加速度、反馈时效、到位与超时检查。
- `scripts/control_linux.sh joint-plan` 只检查实测配置和目标；`joint-run` 才会在固定的
  `.venv-training` 中连接官方驱动。配置不完整、方向与固定驱动不符、记录文件缺失或停止
  行为未实测都会在创建 CAN 控制器前失败。
- 当前 `config/hardware.template.json` 仍为 `control_enabled=false`、实测项为空；USB-CAN
  尚未接入，所以没有执行真机连接、使能、校准或运动。笛卡尔抓取和策略回放仍未接真机。

## 13. 相机型号确认（2026-10-15）

以下为型号刚确认时的快照；SDK、采集和插件的后续实现状态见第14节。

- 用户确认相机为 **Orbbec Gemini 2**；已同步到 README、硬件模板、现场记录和训练文档。
- 官方 Orbbec SDK v2 支持 Gemini 2；本次查询支持表列最低固件 1.4.92、推荐 1.4.98。
  实际设备固件尚未读取，未执行固件升级。官方来源见 `docs/camera-gemini2.md`。
- 本次检查 `.venv` 和 `.venv-training` 均未安装 `pyorbbecsdk`；当前 WSL `lsusb` 没有
  枚举到 USB 设备，不能据此判断 Windows 是否已连接相机。
- 固定 LeRobot 源码没有原生 Orbbec 相机后端；RGB 的 OpenCV 路径仍需现场验证，完整
  RGB-D 需 SDK 采集适配。当前只是确认型号和接入方案，尚未实现或验收实时采集。
- ACT 首版仍用 `observation.images.scene` RGB，深度用于几何定位/标定另行保存。
  不更改已验证的合成数据字段和训练依赖锁定文件。

## 14. Gemini 2 软件适配与 USB-CAN 查询（2026-10-15）

- `.venv` / `.venv-training` 已安装官方 `pyorbbecsdk2==2.0.18`，导入名 `pyorbbecsdk`。
  2.1.x 额外约束的 PyAV/NumPy 与本工程锁定依赖冲突，选用无此依赖冲突的 2.0.18，
  两份环境锁定文件已记录实际版本。重新安装入口为 `scripts/bootstrap_camera.sh`。
- `src/rebot_tea/orbbec.py` 实现 SDK 设备/模式枚举、精确流模式选择、同步帧/D2C、
  去畸变、设备时间戳顺序与RGB/深度偏差检查、实际深度比例与内参导出。
- `src/rebot_tea/lerobot_orbbec.py` 提供 `orbbec_gemini2` 配置与有时效检查的后台采集，
  RGB/深度保持同一帧组，通信异常锁定错误。通过 `scripts/camera.sh lerobot` 先注册插件
  再调用官方record/teleoperate，未修改固定上游源码。
- `scripts/camera.sh check|list|capture|test`、VS Code 相机任务和专用依赖文件已补齐。
  `verify_all.sh --with-training` 加入相机 SDK 和插件测试；聚合报告包含这些结果。
- 官网快速入门明确套件按PCAN-USB、1Mbps使用，BOM采购链接为预装PCAN固件转换板，
  未公布唯一商品料号。证据和现场核对步骤见 `docs/usb-can-identification.md`。
  硬件模板只填写官方参考信息，实际 `usb_can_model/usb_vid_pid` 继续为空。
- 当前SDK实际枚举为零台相机，未验证真实采流/外参。所有导出标定的 `T_base_camera`
  初始为null，不能直接进行基座三维定位。未校准、刷固件、配置总线或移动实物机械臂。
- 完整验收 `bash scripts/verify_all.sh --with-training` 已返回0：主环境92项通过，
  11项专用集成测试在训练环境分别通过（4项视频、7项相机插件），共103项；
  CPU/GPU视觉、独立运动学、ACT合成100步和检查点/预处理器重载通过。
  可纳入Git的摘要：`docs/reports/camera-preparation-2026-10-15.json`；
  原始日志：`artifacts/camera-preparation-verification.log`。

## 15. 原厂配件确认与软件/插件复核（2026-10-15）

- 用户确认全部配件为原厂套件，已写入 `config/hardware.template.json` 的
  `accessories_provenance`；按官方 PCAN-USB 路线准备，实际 VID/PID、固件、限位和校准
  保持待测，`control_enabled/hardware_verified` 继续为 false。
- 新入口 `bash scripts/audit_software.sh` 与同名 VS Code 任务：检查三个环境的 pip/锁定
  版本、固定上游提交、CUDA、MotorBridge ABI、相机 SDK、Notebook 内核、驱动模块、
  udev 和两端编辑器扩展；Windows 由只读 `scripts/audit_windows.ps1` 查询。
  当前27项软件检查全部通过；硬件缺席独立记录，不冒充通信验收通过。
- 发现 Jupyter 扩展虽已安装，但工程环境缺内核。新增
  `scripts/bootstrap_notebooks.sh`，两环境安装固定 ipykernel 6.30.1/nbformat 5.10.4，
  实际传递依赖补入两份锁定文件。已注册 `rebot-tea/rebot-training` 两内核并实际执行
  CUDA 矩阵运算、OpenCV/NumPy/SDK 导入；训练内核设置本地 FFmpeg 库路径。
- 已用 `scripts/configure_camera_permissions.sh` 安装 SDK 自带官方 udev 规则并重载，
  不触发设备、刷固件或修改电机参数。工程安装/控制入口清除继承的系统 ROS Python 路径，
  系统 ROS 和原有编辑器配置保留。
- Windows 实测已安装 usbipd-win 5.3.0（服务运行）、PEAK-Drivers 5.1.3.1189，
  PCANBasic.dll x64/x86 5.1.0.1194，x64 DLL 实际加载通过。历史文档“未安装”已标为旧状态。
  旧位置未找到 Windows `.venv-control-win`，当前 WSL 路线不依赖它；原生备选需重新 setup。
- 缺失的官方 OrbbecViewer 2.7.6 已下载并解压到
  `D:\reBot-tools\OrbbecViewer-v2.7.6\OrbbecViewer.exe`，记录压缩包/EXE SHA-256；
  换机可用 `scripts/prepare_orbbec_viewer.ps1`。未打开相机或刷写固件。
- 依赖变更后完整重跑 `bash scripts/verify_all.sh --with-training` 返回0：
  主环境92项，训练环境补跑4项视频和7项相机插件，共103项全部通过；视觉、运动学、
  ACT 合成100步batch2与重载通过。最新损失/显存见摘要，不覆盖上一轮历史结果。
- 最新说明 `docs/software-environment-check.md`；可纳入 Git 的摘要
  `docs/reports/software-environment-2026-10-15.json`；本地完整报告
  `artifacts/software-audit.json`、`artifacts/prehardware-report.json`；日志
  `artifacts/software-audit.log`、`artifacts/software-audit-verification.log`。
- 当前 Windows/WSL 均无 Gemini 2/USB-CAN 枚举记录。下一步依次做 Windows 相机预览、
  实际 USB 身份与独占附加、WSL 采流与 CAN 只读反馈，再按文档现场校准。
  本轮未配置实际总线、查询电机、校零、写参数或运动；未提交/推送既有未提交改动。

## 16. 优先空茶壶抓放与无实物缺口检查（2026-10-15）

- 用户要求先完成抓取，检查暂无实物时还能做的工作。README 当前目标改为空茶壶抓稳、
  抬升、放回；倒茶/液位代码和记录保留，本阶段不扩展。
- 按实际代码确认：规划只有五步且止于抬升；RGB-D回放仍筛选cup；没有茶壶识别/抓取姿态；
  IK只在检查脚本里做局部回环；未实现碰撞检查和视觉到真实抓放的全链路。
- 已用延迟替代后端复现并修复反馈时间检查问题：控制器改用读取完成后的时间检查反馈和
  总超时；读取期间时钟倒退会终止；LeRobot反馈保守使用整组读取开始时刻，防止读取耗时
  被隐藏。设备侧帧年龄与实际控制频率仍需现场测量。
- 新增5项回归测试，修复前全部失败、修复后通过。完整离线验收以及4项视频/7项相机插件
  集成测试通过：108个不同测试通过（主环境97项，另环境11项）；未重跑ACT100步。
- `docs/grasp-readiness.md` 逐项记录可以继续离线开发的内容：完整抓放状态机、TCP/IK映射、
  路径与碰撞、茶壶标注/训练接口、标定工具、统一无运动演练。这些为待开发项，尚未完成。
  实物参数、抓持反馈和20次至少18成功验收继续留待现场。
- 可随Git保存的摘要：`docs/reports/grasp-readiness-2026-10-15.json`；本地日志
  `artifacts/grasp-readiness-offline.log`、`grasp-readiness-data-tests.log`、
  `grasp-readiness-camera-tests.log`、`grasp-timing-before-fix.log` / `grasp-timing-after-fix.log`。
  本轮未连接或移动机械臂，未提交/推送工作区的既有改动。

## 17. 全部工程成果同步（2026-10-15）

- 用户明确要求全部同步 GitHub，范围包含此前各轮未提交的代码、测试、配置、依赖锁定、
  工作区、Word和Markdown文档及精简检查报告；已授权将原有暂存改动一起纳入。
- 已通过现有 Windows Git/GCM 认证读取私有远端，远端main为
  `8425f122ce97fd20e774dc41c0aadded2f225d1e`，与本地基线一致，无需强推或重写历史。
- 本次提交前完整验收与精简摘要见 `docs/reports/github-sync-2026-10-15.json`，
  本地日志为 `artifacts/github-sync-verification.log`。
- 原始环境、模型、数据、检查点、查看器及缓存继续排除；相机/控制/Notebook安装入口和
  软件锁定版本可用于重建。当前所有实物验收状态仍为未验证，抓取待开发清单继续保留。
- 完整验收退出码0：108个不同测试、CPU/GPU视觉、运动学、关节故障模拟、Gemini 2插件、
  合成ACT100步batch2及策略/预处理器重载通过。训练损失5.336873769760132，验证损失
  1.1031168699264526，末步显存峰值1011996160字节；仍不证明物理抓取成功。
- 实现内容已提交为 `f6c6d95c05c95bd3c85380385a9cf6cdaeab6dfc`（61个文件），
  并普通推送至既有GitHub仓库main。随后于2026-10-15 13:29:00 UTC独立查询远端main，
  与本地HEAD完全一致，工作区干净；未强推、未改变仓库可见性。
- 推送原始输出和远端核验保存在本地 `artifacts/github-sync-push.log` 与
  `artifacts/github-sync-remote-verification.json`。本段成功确认记录以额外文档提交保存，
  并继续普通推送；日后新增代码仍需再次提交/同步。
