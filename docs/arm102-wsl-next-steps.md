# Arm102 → B601-RS：从当前状态继续到示教录制

更新：2026-10-23。运行环境：WSL 2 / Ubuntu 22.04。
项目目录：/home/zsmdtr/projects/rebot-tea。

## 1. 已经完成，当前无需重做

| 项目 | 本次实测结果 |
|---|---|
| Arm102 主臂接入 | CH340 已转接 WSL；/dev/ttyUSB0；账户有串口权限 |
| 主臂通信 | 0–6 号舵机全部响应，角度读取和同步读取有效 |
| 主臂 LeRobot 校准 | 2026-10-23 已保存 leader.json；校准后单独角度读取均在零位附近 |
| B601-RS 通信 | can0，1 Mbps；1–7 号电机全部响应，位置读取有效 |
| 大臂零点 | 用户确认已经完成；本轮沿用，没有写零 |
| 从臂 LeRobot 校准 | 2026-10-23 已重新确认零位并保存 follower.json；校准后 1–7 号位置均接近 0 rad |
| RS 参数 | 调整 7 项并回读、保存；重新连接后复读全部 154 项 |
| 第三视角 | Gemini 2，序列号 AY6R4630030 |
| 腕部相机 | Gemini 305，序列号 CV2F8610009M |
| 双机采集 | 640×480 RGB，配置 30 fps；同时运行约 10 秒，两路均有有效图像 |
| 相机处理配置 | 将 OpenCV 线程数限制为 1，已加入项目相机启动入口 |

主臂可以使用下面这个路径，避免仅依赖 ttyUSB0 编号：
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0。
这个适配器没有独立序列号；如果以后接入第二个同型号 CH340，需重新确认映射。

本轮结束时大臂已发送失能命令，写入后未再次使能。
参数保存命令已成功返回，保存后复读通过；尚未做断电重启后的持久化验证。

## 2. 本轮到底改了哪些电机参数

采用 MotorBridge Studio 的 B601-RS 模板为参照，
版本 23b6e62ab8173faee3928c827cca3b7f42ba2128。

| 电机 ID | 参数 | 原值 | 写入并回读的值 |
|---|---|---:|---:|
| 1 | vel_max（位置模式最大速度，rad/s） | 10 | 1 |
| 2 | vel_max（位置模式最大速度，rad/s） | 10 | 0.4 |
| 3 | vel_max（位置模式最大速度，rad/s） | 10 | 0.4 |
| 2 | EPScan_time（主动反馈周期编码） | 3 | 0 |
| 4 | EPScan_time | 0 | 3 |
| 5 | EPScan_time | 0 | 3 |
| 6 | EPScan_time | 0 | 3 |

按用户确认的方案，保留 1 号电机 limit_spd=0.1 rad/s。
官方模板该项为 10 rad/s，因此当前配置是“模板参数＋保留较低速度限制”，不能称为与模板完全一致。
其他 153 项读回值与模板一致。EPScan_time 数值是协议编码，不直接等于毫秒。
上述位置模式速度参数不能代替 LeRobot MIT 模式的运动限制与现场调试。

## 3. 先调整相机视野

刚才保存的测试图像中，第三视角大部分被近处物体挡住；腕部画面可见夹爪。
正式录制前：

1. 移开遮挡第三视角的物体，将 Gemini 2 固定好，让画面同时覆盖机械臂操作区域、壶和杯。
2. 将 Gemini 305 固定在腕部，确保夹爪、壶把和接近杯口时的目标区域可见。
3. 避免移动中的 USB 线牵拉手腕。
4. 两台相机安装好以后，训练和使用模型时保持相同视角。

本次双机同时采集的读帧结果约为 30.7 fps（Gemini 2）和 28.3 fps（Gemini 305）。
短时间计数可能包含启动队列中的帧，不代表严格同步或连续稳定达到 30 fps。
Gemini 305 当前实际链路为 USB 2.1 / 480 Mbps，Gemini 2 为 USB 3.0 / 5 Gbps。
可以先做 10 Hz 的短示教联调；正式 30 Hz 采集前需在机器人、编码器和两相机一起工作时复核持续帧率。
如果腕部仍掉帧，再检查 Gemini 305 是否使用了 USB 3 数据线与接口。

## 4. 准备 LeRobot 校准文件

大臂在 MotorBridge 中校零，与 LeRobot 保存校准文件是相关的两件事。
Arm102 主臂和 B601-RS 从臂的 LeRobot 校准均已于 2026-10-23 完成，
分别保存为 leader.json 和 follower.json。正常情况下沿用这两个 id，不要重复写零。

校准需要你在机械臂旁摆放姿态。两臂应按厂家零位图放好，夹爪闭合。
本机当前版本的校准入口在连接时会改变电机状态：
大臂连接会使能，随后进入校准时失能；主臂会解锁并重置多圈计数。
因此下列命令要在可靠支撑、空夹爪、操作区域清空的现场条件下逐个执行。

官方零位图：
- [Arm102 主臂零位](https://files.seeedstudio.com/wiki/robotics/projects/lerobot/102_zeroposition.jpg)
- [B601 从臂零位](https://files.seeedstudio.com/wiki/robotics/projects/lerobot/b601dm_zeroposition.jpg)
  （官方 LeRobot 文档说明图片为 DM，RS 使用相同关节布局。）

先停止其他控制程序。在运行 MotorBridge Gateway 的原终端按 Ctrl+C 退出；
同时关闭 MotorBridge Studio 的连接，避免与 LeRobot 同时控制。

在 VS Code 的 Ubuntu / WSL 终端中准备环境：

~~~bash
cd /home/zsmdtr/projects/rebot-tea
export LD_LIBRARY_PATH="$PWD/.venv-training/lib"
~~~

主臂校准（已经完成，正常情况下不要重复执行）：

~~~bash
.venv-training/bin/lerobot-calibrate \
  --teleop.type=rebot_102_leader \
  --teleop.port=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --teleop.id=leader
~~~

出现“Manually move the reBot Arm 102 to its zero pose and close the gripper”时，
按零位图确认小臂姿态和闭合夹爪，然后按回车。看到“Calibration saved”才算保存成功。

大臂 LeRobot 校准（已经完成，正常情况下不要重复执行）：

~~~bash
.venv-training/bin/lerobot-calibrate \
  --robot.type=rebot_b601_follower \
  --robot.motor_family=rs \
  --robot.port=can0 \
  --robot.id=follower
~~~

这条命令会重新定义大臂当前姿态为零点。
仅在确实需要创建该校准文件、且大臂已经恢复到厂家零位时执行。
看到“Press ENTER when ready”时确认姿态后再按回车，等待“Calibration saved”。

默认保存位置：

~~~text
/home/zsmdtr/.cache/huggingface/lerobot/calibration/teleoperators/rebot_102_leader/leader.json
/home/zsmdtr/.cache/huggingface/lerobot/calibration/robots/rebot_b601_follower/follower.json
~~~

后面的跟随、录制命令一直使用 leader 和 follower 这两个 id。

## 5. 先确认主从跟随，再录数据

两臂回到匹配的起始姿态，夹爪为空，现场已确认失能后的支撑方式。
下面是用于初次观察的 10 Hz、10 秒、小幅目标变化模板；方向、限位和控制增益仍需现场确认。
启动命令会使能从臂。先支撑从臂，确认双臂起始姿态接近、工作区无人和障碍物，
并让操作者站在现场能立即停止的位置。`max_initial_target_delta=5` 要求第一次动作的
主从对应关节偏差不超过 5°；超过时程序拒绝发送动作并尝试失能。
`max_relative_target=0.5` 是每次指令相对当前反馈位置的限幅，不能替代物理急停或独立速度限制。

~~~bash
cd /home/zsmdtr/projects/rebot-tea
bash scripts/camera.sh lerobot teleoperate \
  --robot.type=rebot_b601_follower \
  --robot.motor_family=rs \
  --robot.port=can0 \
  --robot.id=follower \
  --robot.max_initial_target_delta=5 \
  --robot.max_relative_target=0.5 \
  --teleop.type=rebot_102_leader \
  --teleop.port=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --teleop.id=leader \
  --fps=10 \
  --teleop_time_s=10 \
  --display_data=false
~~~

你需要逐项观察：

1. 先小幅转动小臂的一个关节，确认大臂对应关节朝正确方向运动。
2. 每次只换一个关节，检查六个关节。
3. 小幅开合小臂夹爪，检查大臂夹爪方向和范围。
4. 检查停止操作、退出程序时的实际行为。默认退出会失能，须有支撑。
5. 10 秒后程序正常退出；需要提前停止时按 Ctrl+C。退出默认会失能，必须先支撑从臂；
   主从异常时按现场既定的独立停止方式处理，不能只依赖终端键盘。

10 秒测试通过后，可在同一命令中去掉 `--teleop_time_s=10` 持续跟随，
仍须有人全程看护。主从方向或停止行为不符合预期时，先排查，不进入录制。

本机所装主臂驱动在读取失败时可能继续返回缓存角度。
因此“关闭程序”和“通信中断”各自的行为必须现场确认后，才能无人值守运行。

## 6. 在这个入口录下你对机械臂的操作

你操作 Arm102；同一个录制程序控制 B601-RS，
同时保存两路图像、从臂实际状态和示教动作目标。
首次只做空壶抓取、放回的 5 条短演示，先确认整条链路。

执行前先结束上一步 teleoperate。
下面的 data/tea_dual_001 必须尚不存在；如已存在，换新的编号，并同时修改 repo_id 和 root。

~~~bash
cd /home/zsmdtr/projects/rebot-tea
bash scripts/camera.sh lerobot record \
  --robot.type=rebot_b601_follower \
  --robot.motor_family=rs \
  --robot.port=can0 \
  --robot.id=follower \
  --robot.max_initial_target_delta=5 \
  --robot.max_relative_target=0.5 \
  --teleop.type=rebot_102_leader \
  --teleop.port=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --teleop.id=leader \
  --robot.cameras="$(cat config/cameras.teapot-dual.json)" \
  --dataset.repo_id=local/tea_dual_001 \
  --dataset.root=/home/zsmdtr/projects/rebot-tea/data/tea_dual_001 \
  --dataset.no_stamp=true \
  --dataset.single_task="Pick up an empty teapot and put it back" \
  --dataset.num_episodes=5 \
  --dataset.episode_time_s=30 \
  --dataset.reset_time_s=20 \
  --dataset.fps=10 \
  --dataset.push_to_hub=false \
  --display_data=false
~~~

此处数据集/控制循环为 10 Hz，两台相机配置仍为 30 fps；
它是首次联调的抽样方案，不是已经验证的最终采集频率。
如需正式采集 30 Hz，先完成整套硬件下的持续性能检查。

程序连接完成后自动开始回合，没有单独等待“开始录制”的按钮。
看到 Recording episode 0，就用小臂演示“接近空壶→夹住→抬起→放回→松爪”。
看到 Reset the environment 时恢复场景；该阶段可遥操作但不记录训练帧。

支持键盘监听时：右方向键提前结束当前阶段；左方向键重录当前未保存回合。
Esc 通常仍会保存当前回合，不能把它当作“丢弃失败演示”。
退出前将机械臂妥善安置，按已验证的方式停止和支撑。

## 7. 数据保存在哪里，怎样接到训练

录制数据保存在：

~~~text
/home/zsmdtr/projects/rebot-tea/data/tea_dual_001/
~~~

元数据应有 observation.images.scene、observation.images.wrist、observation.state、action。
检查两路回放、动作完整性、图像更新和时序；失败演示应排除。
同一回合的两个视角不等于硬件同步，当前实现也不检查两相机之间的时间差。

完成 5 条有效演示后，先做 100 步短训练，确认数据能够被 ACT 读取。
回合 0–3 用于此检查，回合 4 留出；这条命令不会自动计算留出集指标。

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

训练输出目录也需使用新目录。100 步和 5 条演示用于验证采集到训练的连接，
不能据此判断模型已经会倒水。正式倒水模型需要覆盖目标壶、杯位置和倒水阶段的真实演示。

## 本次仍未完成

- 现场逐轴跟随、夹爪范围和停止行为检查。
- 首次逐轴主从跟随、夹爪范围和停止行为检查。
- 遮挡移除后的第三视角画面检查。
- 带机械臂、视频编码的长时间双机录制。
- 真实示教数据、正式训练与模型部署。
- “不透明杯装至容量 90%”所需的反馈方案。

参考：
- [B601-RS 官方初始化流程](https://wiki.seeedstudio.com/cn/rebot_b601_rs_getting_started/)
- [本机锁定版本 LeRobot B601 文档](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/docs/source/rebot_b601.mdx)
- [本轮核对的 RS 参数模板](https://github.com/motorbridge/motorbridge-studio/blob/23b6e62ab8173faee3928c827cca3b7f42ba2128/src/lib/robotArm.js)
