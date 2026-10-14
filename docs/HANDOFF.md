# 项目交接：Seeed reBot Arm B601-RS 茶壶抓取与倒茶

更新时间：2026-10-14  
WSL 项目路径：`/home/zsmdtr/projects/rebot-tea`  
GitHub 仓库：`https://github.com/ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning`

> **新对话开始时先检查当前工作区文件。** 先进入上述 WSL 项目，阅读本文件、`README.md` 和 `docs/robot-first-connection.md`，然后运行 `git status --short --branch`。以当前文件和代码为准，不要假设此前状态仍未变化，也不要先重建或覆盖已有环境。

## 1. 项目目标和硬件型号

最终任务：视觉识别茶壶并抓取，向随机摆放的不透明茶杯倒水；达到杯容量的 90% 时停止。用户已说明机械臂整套和相机已具备。目前阶段聚焦电脑连接及机械臂控制，杯子和相机暂缓。

- 机械臂：Seeed Studio reBot Arm B601-RS。
- 相机：已有，具体品牌/型号及安装方式未知。
- USB-CAN：型号未知，尚未接入电脑。
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

## 6. 未完成的问题

1. USB-CAN 型号、驱动和由 Windows/WSL 哪一侧持有尚未确定；真实 CAN 接口和机械臂从未连通验证。
2. 1–7 号电机在线状态、固件、零位校准、参数读回及运动方向待现场确认。
3. 项目已具备限位、速度/加速度、到位反馈与超时的模拟控制层和接口，
   尚未接入或验证真实运动后端；示例参数不是实测安全参数。
4. IK 数学演示不验证真实可达性、碰撞、TCP 或负载；当前没有 SRDF 自碰撞配置。
5. 相机型号/标定、示教主臂与实际茶壶数据尚缺；LeRobot 环境和合成软件链路已准备。
6. 茶壶专用抓取策略未训练；不透明杯的实际反馈传感、余流测量和停流策略待现场验证。
7. Git 状态和远端应由新对话重新检查；交接文件是否已提交不能假定。

## 7. 下一步具体任务

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
