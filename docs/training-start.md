# 拿到实物后怎样开始训练

控制机械臂和训练模型是两个阶段。先通过robot-first-connection.md中的通信、校零、
参数和小幅运动验收；此时不需要相机或训练。不要先启动训练代码来判断接线是否正确。
完整阶段顺序、仍需开发的真机执行与现场通过条件见 [实物工作流程](after-hardware-workflow.md)。

本项目已提供离线视觉/几何、受限关节模拟和原生 LeRobot 合成数据训练链路，
没有完成真实示教采集接入，也没有已经训练好的抓壶策略。
合成软件验收见 prehardware-development.md；以下是在实物验收后的实施步骤。

## 1. 第一个学习任务只做空茶壶抓放

先固定壶位置和放回位置，只学“抓起空壶，短距离抬升，再放下”。
先采3–5条成功演示检查整条数据链路，不把这几条或100步短训练当成已学会。
链路跑通后再覆盖不同位置/方向，最后才增加盛水重量与倾倒。

这不是目标检测图片训练：模仿学习需要同步图像、机械臂关节状态和人给出的动作目标。
只拍茶壶照片可以训练识别，却不足以训练机械臂如何运动。

## 2. 确认有没有示教主臂

直接PC控制不需要主臂。官方标准遥操采集使用reBot Arm102主臂带动B601-RS从臂。
如果套件里只有B601-RS，没有主臂，先完成PC控制；不能直接照抄主从遥操采集命令。
若采用拖动或其他输入设备，需另外验证同步动作/观测/视频采集，不把重力补偿功能
等同于已经具备可训练示教数据。

图形界面入门可参考[Seeed Arm102+B601-RS训练指南](https://wiki.seeedstudio.com/cn/sensecraft_robotics_rebot_arm_102_b601_rs/)：
设备校准→验证遥操→录制完整episode→检查数据→ACT训练→实机评估。
云训练可能涉及平台费用；本项目不代替你购买或上传数据。

## 3. 独立训练环境，固定版本

保留现有Python3.10项目环境；独立Python3.12训练环境现已建立，为项目内的
`.venv-training` Conda前缀环境，通过脚本直接调用，不需要激活或重新创建。
固定huggingface/lerobot提交
ae2b57f12223c8c5184fcd814eb63cd9ce7de203（2026-10-14查询）。

现有机器检查与换机初始化：

```bash
bash scripts/training.sh check
# 仅在换机/环境缺失时初始化：
bash scripts/bootstrap_training.sh
```

当前主仓库内置名称为rebot_b601_follower（同时motor_family=rs）和rebot_102_leader。
不要混用旧Seeed外部插件名称seeed_b601_rs_follower/rebot_arm_102_leader。
初始化脚本安装rebot、training、core_scripts依赖并锁定版本；单装rebot不包含完整训练/数据功能。
本轮已通过合成数据100步GPU训练及检查点重载，真实示教训练尚未进行。
配套[固定版本设备说明](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/docs/source/rebot_b601.mdx)。

## 4. 校准与遥操

实际CAN假设为can0、主臂串口假设为/dev/ttyUSB0，需替换成真实设备。
这些命令会涉及实物校准或运动，仅在机械台架与前述验收就绪后执行：

```bash
export LD_LIBRARY_PATH="$PWD/.venv-training/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
.venv-training/bin/lerobot-calibrate --robot.type=rebot_b601_follower --robot.motor_family=rs --robot.port=can0 --robot.id=follower
.venv-training/bin/lerobot-calibrate --teleop.type=rebot_102_leader --teleop.port=/dev/ttyUSB0 --teleop.id=leader
.venv-training/bin/lerobot-teleoperate --robot.type=rebot_b601_follower --robot.motor_family=rs --robot.port=can0 --robot.id=follower --teleop.type=rebot_102_leader --teleop.port=/dev/ttyUSB0 --teleop.id=leader
```

按该版本的官方校准姿态操作，保持同一robot.id/teleop.id以复用标定。
先验证主从每个关节的小幅跟随、夹爪开合和停止，再录数据。

## 5. 接入相机并记录演示

控制阶段可不使用相机；训练视觉策略时必须恢复相机和同步视频。
运行`.venv-training/bin/lerobot-find-cameras opencv`确定摄像头，保持安装位置与字段名称一致。
使用官方记录命令`.venv-training/bin/lerobot-record`，配置RS从臂、实际主臂、实际camera以及：

- dataset.repo_id=local/empty-teapot-test。
- dataset.root指向一个新数据集目录，dataset.single_task描述空壶抓放。
- 先num_episodes=5，push_to_hub=false，数据先留本机。
- 帧率应在相机/控制采样可稳定达到的范围内，不能只改配置就认为同步。

每条人工检查：抓稳、动作完整、图像连续、关节/动作字段正确、没有异常跳变。
故障/缺帧或失败演示重录；按整条episode或独立物体摆位划分训练与验证，避免相邻帧泄漏。

## 6. 用ACT做一次短训练验证

准备好真实LeRobotDataset后，才执行下列示例；当前仅执行了合成软件训练检查。
下列命令应在训练环境中运行，直接使用 `.venv-training/bin/lerobot-train`，
并参考 scripts/training.sh 中训练进程的库路径设置；实际相机字段和公共关节单位先核对。

```bash
.venv-training/bin/lerobot-train \
  --dataset.repo_id=local/empty-teapot-test \
  --dataset.root=/absolute/path/to/recorded-dataset \
  --policy.type=act \
  --policy.device=cuda \
  --policy.push_to_hub=false \
  --batch_size=2 \
  --steps=100 \
  --wandb.enable=false \
  --output_dir=outputs/train/act-empty-teapot-smoke
```

把数据路径换成实录数据，输出用新目录。100步仅检查数据读取、损失、保存与显存，
不是正式训练。RTX5060笔记本8GB显存可先小batch尝试，但不保证任意模型或多相机配置适配。
正式训练步数和演示数量由留出场景成功率决定，损失降低不等于实物任务成功。

配套[固定版本ACT说明](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/docs/source/act.mdx)。

## 7. 部署前先评估，再逐步扩展

检查点保存后先核对关节单位/顺序、动作限幅、反馈时效和停止处理，
再低速空载评估未见过的演示位置并记录成功率。当前项目没有替你接通真实policy rollout。
后续顺序为：空壶抓放→冷水倾倒→目标位置变化→液位/容量反馈控制。
本阶段先不处理杯子，不让尚未验证的模型直接运行完整倒茶任务。
