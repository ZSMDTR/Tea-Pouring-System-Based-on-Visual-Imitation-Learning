# 双相机 LeRobot 录制与 ACT 训练

用户已确认：腕部相机是 Gemini 305，第三视角相机是 Gemini 2，示教输入为 Arm102 主臂。
主臂已转接 WSL，0–6 号舵机检查通过；2026-10-23 已保存主臂 leader.json
和从臂 follower.json。下面的 `rebot_102_leader` 命令还须在逐轴跟随检查完成后使用。
标准 `lerobot-record` 在当前锁定版本中必须指定 teleop，不能仅连接两台相机就录动作。
录制和训练均在打开本工程的 VS Code Ubuntu/WSL 终端中运行。

2026-10-23 两相机均已进入 WSL，实际序列号已写入配置：Gemini 305 为 CV2F8610009M，
Gemini 2 为 AY6R4630030。640×480 RGB、配置 30 fps 的双机同时采集约 10 秒通过；
限制 OpenCV 为 1 个线程后，读帧吞吐约为第三视角 30.7 fps、腕部 28.3 fps。
短测不代表硬件同步或长时间稳定 30 fps；Gemini 305 当前为 USB 2.1，Gemini 2 为 USB 3.0。
USB-CAN 为 candleLight (`1d50:606f`)，can0 已配置 1 Mbps，1–7 电机只读检查通过。
用户确认大臂已校零；本轮另完成 7 项 RS 参数调整、回读和保存，保留 1 号低速限制。
后续现场步骤和本机可复制的命令见 [Arm102 / WSL 接入与示教步骤](arm102-wsl-next-steps.md)。

## 1. 相机与数据字段

| 安装位置 | 相机 | 配置名称 | 数据集字段 |
|---|---|---|---|
| 第三视角，固定观察桌面 | Orbbec Gemini 2 | scene | observation.images.scene |
| 腕部，观察夹爪与物体 | Orbbec Gemini 305 | wrist | observation.images.wrist |

一次演示保存两路 RGB、机械臂实际状态和示教动作目标。录制、训练、部署时保持
scene/wrist 名称、对应视角、图像处理、关节顺序和单位一致。

双相机配置在 config/cameras.teapot-dual.json，已绑定上述实际序列号。
640×480、RGB、30 fps 请求模式已经实机开流；持续录制频率仍需在完整控制与编码负载下确认。

## 2. 打开电脑上的采集入口

在 VS Code 打开 Ubuntu/WSL 工程文件夹，选择“终端 → 新建终端”：

~~~bash
cd /home/zsmdtr/projects/rebot-tea
bash scripts/camera.sh list
~~~

这条命令只枚举相机和流模式，不发送机械臂命令。列表应同时出现 Gemini 2 和
Gemini 305。Windows 能看见设备不代表 WSL 已接入；须将两台相机分别附加到 Ubuntu。
先在 Windows 的 Orbbec Viewer 分别核对型号、固件、RGB/深度模式；关闭 Viewer 后，
在管理员 PowerShell 用 `usbipd list` 找到两台相机各自的 BUSID，分别运行
`usbipd bind --busid <实际BUSID>`；普通 PowerShell 分别运行
`usbipd attach --wsl --busid <实际BUSID>`。BUSID 会变化，不要把历史编号写死。
WSL 中用 `lsusb` 检查两台都已进入 Ubuntu。详细步骤见
[Gemini 2 接入说明](camera-gemini2.md)。也可在“终端 → 运行任务”选择
“Orbbec：枚举相机和流模式”。

根据枚举结果更新双相机配置，以及各单机配置的分辨率、格式、帧率和序列号。
两路使用相同图像尺寸和稳定可达到的采集频率。再分别采集少量图像：

~~~bash
bash scripts/camera.sh capture --config config/camera.gemini2.json --frames 30
bash scripts/camera.sh capture --config config/camera.gemini305.json --frames 30
~~~

以上仅检查相机，不包含机械臂动作。每条命令结束后再运行下一条。

## 3. 开始录制你的操作

仅在确有 Arm102 主臂时，先完成主从臂校准、逐轴跟随、夹爪和退出行为验证，并沿用现场验证后的关节限位、
动作变化上限、控制参数与退出设置。录制程序本身负责主从跟随；关闭占用同一设备的
其他控制、遥操作程序和 Gateway。

下面是 **Arm102 条件模板**：can0、/dev/ttyUSB0、follower、leader 要替换为实际接口和已有校准 ID。
相机参数须按第 2 步确认；示例未替你填写尚未实测的控制参数。

~~~bash
cd /home/zsmdtr/projects/rebot-tea
bash scripts/camera.sh lerobot record \
  --robot.type=rebot_b601_follower \
  --robot.motor_family=rs \
  --robot.port=can0 \
  --robot.id=follower \
  --teleop.type=rebot_102_leader \
  --teleop.port=/dev/ttyUSB0 \
  --teleop.id=leader \
  --robot.cameras="$(cat config/cameras.teapot-dual.json)" \
  --dataset.repo_id=local/tea_dual_001 \
  --dataset.root=/home/zsmdtr/projects/rebot-tea/data/tea_dual_001 \
  --dataset.no_stamp=true \
  --dataset.single_task="Pick up an empty teapot and put it back" \
  --dataset.num_episodes=5 \
  --dataset.episode_time_s=30 \
  --dataset.reset_time_s=20 \
  --dataset.fps=30 \
  --dataset.push_to_hub=false \
  --display_data=false
~~~

如果实际采用不同帧率，录制 fps 与相机配置一并核对。最终数据目录 tea_dual_001
由录制程序创建，新建时必须尚不存在。下一批可使用 tea_dual_002，并同时修改
repo_id 和 root。

启动前确认不需要图形显示，且现场看护、机械臂支撑与停止方式已到位。程序会连接
主臂与从臂，连接流程可能使能电机；当前不能把此模板当作无硬件检查命令。
看到 Recording episode 0，用已验证的 Arm102 控制 B601-RS：
接近空壶 → 夹紧 → 抬起 → 稳定保持 → 放回 → 松爪。

看到 Reset the environment，恢复茶壶和机械臂起始状态。复位阶段仍可遥操作，
但不向演示写入帧。随后进入下一条，直到保存 5 条。

支持键盘监听时，右方向键提前结束当前录制或复位阶段；左方向键要求重录当前未保存
的回合。Esc 结束整场录制，但通常仍会保存当前回合，不能用于表示“丢弃失败演示”。
退出时按已验证的方式支撑、安置机械臂。

本版本保存的是处理后的示教动作目标，不能假定是驱动再次限幅后实际下发的目标。
录制时避免持续顶住限位，并核对示教目标、执行目标和关节实际运动的一致性。

## 4. 找到刚录的数据

在 VS Code 左侧展开 data/tea_dual_001，对应完整路径：

~~~text
/home/zsmdtr/projects/rebot-tea/data/tea_dual_001/
~~~

meta/info.json 的 features 应包含：

~~~text
observation.images.scene
observation.images.wrist
observation.state
action
~~~

回放两路视频：scene 是固定第三视角，wrist 随腕部运动。检查动作完整、图像无长时间
冻结、两路事件时序对应、抓持成功。失败或中断回合要标记并排除。

## 5. 让 ACT 读取同一个数据目录

首次使用 5 条有效演示检查链路。以下取回合 0、1、2、3 训练，回合 4 保留作后续
独立评估；编号须与实际数据一致。此命令不计算回合 4 的验证指标。正式训练另做
独立场景划分，并对留出集及真实抓取分别评估。

~~~bash
cd /home/zsmdtr/projects/rebot-tea
export LD_LIBRARY_PATH="$PWD/.venv-training/lib"
.venv-training/bin/lerobot-train \
  --dataset.repo_id=local/tea_dual_001 \
  --dataset.root=/home/zsmdtr/projects/rebot-tea/data/tea_dual_001 \
  --dataset.episodes='[0,1,2,3]' \
  --policy.type=act \
  --policy.device=cuda \
  --policy.pretrained_backbone_weights=null \
  --policy.push_to_hub=false \
  --batch_size=1 \
  --steps=100 \
  --save_freq=100 \
  --wandb.enable=false \
  --output_dir=/home/zsmdtr/projects/rebot-tea/outputs/train/tea_dual_act_001
~~~

使用尚不存在的输出目录。双相机会增加显存占用，先从 batch_size=1 开始；
实际显存和速度仍需短训练确认。100 步只检查数据读取、损失计算和检查点保存，
不代表模型已经学会任务。

LeRobot 默认根据数据集元数据为 ACT 建立输入特征，两路图像进入同一个 ACT 策略。
核对训练日志中的输入同时包含 scene、wrist；部署时也提供这两个视角。
旧的单视角数据/模型不能通过改名自动变成双视角模型。

训练结果保存在：

~~~text
/home/zsmdtr/projects/rebot-tea/outputs/train/tea_dual_act_001/checkpoints/
~~~

使用正式 lerobot-train 读取实录数据。scripts/training.sh smoke/verify 和
scripts/training_tools.py 仍是固定单路 scene 的合成检查，不用于验证真实双相机训练。

## 6. 如果使用手柄示教

现有 `scripts/gamepad.sh` 是项目自己的 LeRobotDataset 记录入口，使用 Windows 手柄桥；
它目前仅把一台 Gemini 2 录到 `scene`，不等于上面的标准 `lerobot-record`，也不能把
`--teleop.type=gamepad` 加到上述命令。手柄实机控制还需先完成
[手柄遥操作说明](gamepad-teleoperation.md)的现场门禁。若需要两台相机与手柄同时录制，
还需扩展该记录入口并验证两路时间戳和数据字段；在此之前不要把单视角手柄数据与
双视角 ACT 配置混用。

## 7. 现场仍需验证的内容

- 两路画面属于同一回合，但现有接口从各相机读取最新帧，未检查两机之间的时间差，
  也未将各相机设备时间戳写入该标准观测接口，不能称为硬件同步。需要现场检查延迟；
  单台 RGB/深度同步检查不等于两机同步。
- use_depth=false 表示不给 ACT 添加深度输入；现有底层仍采集 RGB 和深度并对齐，
  双台 RGB-D 的 USB 负载与稳定帧率必须实测。
- 正式采集前固定好腕部相机；训练与部署保持安装一致。使用几何定位时，
  第三视角需相机到基座标定，腕部需相机到末端标定。
- 真实模型执行入口和“杯容量 90%”反馈继续按既有实物流程接入。

参考：[固定版本 B601 接入](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/docs/source/rebot_b601.mdx)、
[ACT 配置](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/src/lerobot/policies/act/configuration_act.py)、
[Orbbec SDK 2.0.18 的 Gemini 305 支持](https://github.com/orbbec/pyorbbecsdk/releases/tag/v2.0.18)。
