# 实物前的软件准备

本轮完成受限关节模拟、原生 LeRobot 合成数据、ACT 短训练与检查点重载。
所有动作执行仅使用 mock；控制代码不导入 SDK、不创建 CAN 连接。
生成图像是彩色条形占位图，不是实际茶壶图片或真实成功演示。

## 已有环境与常用入口

主环境 `.venv` 为 Python 3.10，`.venv-kinematics` 专用于 Pinocchio。
`.venv-training` 是项目目录中的独立 **Conda 前缀环境**，Python 3.12；
不需要在终端激活，统一使用启动脚本。

```bash
cd ~/projects/rebot-tea
bash scripts/verify_all.sh --with-training
```

该命令失败时返回非零，缺少训练环境时在运行检查前直接报告缺失。
VS Code 任务“实物前完整验收（含 ACT 合成训练）”执行相同流程。
默认 `bash scripts/verify_all.sh` 保留不依赖 LeRobot 的原有验收，并加入关节模拟。
核心测试中的四项视频集成测试会跳过；带训练的验收会在训练环境单独执行这四项，禁止跳过。

主报告为 `artifacts/prehardware-report.json`，包含各环节结果、软件版本、
数据划分、训练/验证损失、显存和重载结果。构建时的 Git HEAD 与未提交状态也会记录。
所有报告与数据留在本机。

## 重新建立训练环境

现有环境已经建立。换机重建时执行：

```bash
bash scripts/bootstrap_training.sh
bash scripts/training.sh check
```

脚本复用现有 Miniconda，在项目目录建立 Python 3.12/FFmpeg 环境。
上游 LeRobot 固定为 `ae2b57f12223c8c5184fcd814eb63cd9ce7de203`，
安装 `rebot,training,core_scripts`。已存在且提交不同的上游目录会报错，避免覆盖。
Python/原生库锁定在 `requirements/lock-training-conda.txt`（Linux x86_64），
Python 包锁定在 `requirements/lock-training-cu128.txt`；两个 editable 项目由脚本单独安装。
TorchCodec 0.10 先从官方 CPU wheel 源安装，PyTorch 2.10/TorchVision 0.25 使用 CUDA 12.8。

运行脚本只在子进程中清除 ROS 的 PYTHONPATH、设置训练环境库路径，并关闭 Hub/W&B 联网。
初始化依赖需要网络；后续合成验收使用本地输入，ACT 骨干不下载预训练权重。

## 受限关节控制接口

```bash
.venv/bin/python scripts/verify_joints.py
```

`JointState` 包含七轴公共关节位置（rad）、同一单调时钟上的采样时间和可选故障。
`JointLimits` 包含逐轴限位、速度/加速度上限、反馈时效、到位容差/样本数及超时。
`JointBackend` 定义 `read_state`、`send_target`、`request_stop`；本轮仅接受 `mode=mock`。
`LimitedJointController.start` 从当前有效反馈建立轨迹，`tick` 持续检查，`stop` 请求停止。
终态不恢复；另一次运行必须创建新控制器。

顺序固定为 shoulder_pan、shoulder_lift、elbow_flex、wrist_flex、wrist_yaw、wrist_roll、gripper。
五次插值的持续时间由速度和加速度解析极值计算，50 Hz 模拟执行；到位须连续新鲜样本确认。
停止报告区分 `stop_requested` 与 `mock_stop_confirmed`，两者均不证明真机已停止或保持姿态。
模拟故障覆盖反馈过时、缺失、运动卡住和通信失败，报告为 `artifacts/joints-check.json`。

`config/joints.synthetic.json` 只含合成限值。
`config/hardware.template.json` 中实际设备、校准和控制参数留空，control_enabled=false；
这份模板没有运动加载入口，填写它也不会开启真机控制。
现有笛卡尔 MockRobot 步骤保持独立，尚未自动转换为 IK/碰撞验证后的关节轨迹。

## 合成数据、训练和重载

```bash
bash scripts/training.sh generate --dataset-root data/synthetic-new
bash scripts/training.sh validate --dataset-root data/synthetic-new
bash scripts/training.sh smoke --dataset-root data/synthetic-new --output-dir outputs/train/synthetic-new
bash scripts/training.sh test
```

generate 要求新目录，smoke 要求新输出目录，不覆盖已有数据或检查点。
verify 会复用并检查默认数据目录 `data/synthetic-empty-teapot`，每次使用新的训练输出目录。

默认生成 5 条×120 帧、20 Hz、224×224 RGB，七轴字段为 observation.state 与 action，
相机键为 observation.images.scene。action 来自模拟后端实际收到的目标，
50 Hz 控制与20 Hz记录在虚拟时钟上同步。
向 LeRobot 写入前显式将公共关节 rad 转为 deg；夹爪字段也是角度，未伪装成开度百分比。
真实 connection.py 探测给出原始电机 rad，必须现场确认方向/零偏才能转换为公共坐标。

数据侧文件 prehardware.json 声明合成来源、单位和七轴顺序。
训练用 episodes 0–3，验证用 episode 4；检查帧数、字段、时间、数值、视频解码和跳变。
当前自动检查入口针对该合成契约，现场数据需在确认实际字段/单位后接入。

ACT 默认结构，batch size 2、GPU 100步、关闭预训练骨干与上传；
仅遇 CUDA 显存不足时改用 batch size 1，新目录重试一次。
重载模型、预处理器和后处理器后，在留出 episode 上检查损失与七轴预测。
检查点中的 prehardware-smoke.json 明确声明 synthetic=true、physical_task_verified=false。

## 下一步

现场先按 robot-first-connection.md 完成设备识别、通信、校准和小幅运动验收。
记录使用 commissioning-record.md；采集和备份使用 data-collection.md；
任务通过标准见 task-acceptance.md。真实抓取策略训练与水量反馈仍需实际数据和现场验证。
