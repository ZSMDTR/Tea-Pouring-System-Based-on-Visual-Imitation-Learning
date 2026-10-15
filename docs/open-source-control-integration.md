# 开源机械臂控制项目筛选与接入

检索日期：2026-10-15。只评估可追溯的 GitHub 仓库和项目官方文档；实物尚未接入，
本次没有配置 CAN、使能电机、校零或发送运动目标。

## 筛选结果

| 项目 | 适用内容 | 许可证与结论 |
|---|---|---|
| [Hugging Face LeRobot](https://github.com/huggingface/lerobot/tree/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/src/lerobot/robots/rebot_b601_follower) | B601-RS/DM follower、MotorBridge、公共关节角、软限位、相对目标裁剪、反馈与夹爪控制 | Apache-2.0；本项目已经固定同一提交，选为真机驱动层 |
| [Seeed B601 LeRobot 插件](https://github.com/Seeed-Projects/lerobot-robot-seeed-b601/tree/0da1a1a5575905d77823227003ffee7672059780) | 独立安装的 B601 follower 插件 | MIT；README 将 RS 路径标为仍在完善，不替换已经固定并测试的内置实现 |
| [MotorBridge](https://github.com/motorbridge/motorbridge/tree/e2607dc1063bbcfa5ce8433644e6eb6fe8267525) | RobStride/SocketCAN 通信 | MIT；项目继续固定已验证的 `motorbridge==0.5.6` |
| [Seeed reBot-DevArm](https://github.com/Seeed-Projects/reBot-DevArm/tree/fd9036b508b51d396a8ef32fb287c405f5130b6c) | 官方硬件、LeRobot、ROS 2、视觉抓取入口索引 | 作为型号与官方生态索引，不复制整仓库 |
| [Seeed reBot ROS 2 控制器](https://github.com/Seeed-Projects/reBotArmController_ROS2/tree/7506dc0fc5c2a7787dcb974d89d13f9f49d16d61) | MoveIt 2、假驱动、MuJoCo 抓取、安全状态机 | 仓库元数据及根目录均无许可证；只用于比较能力，没有复制代码 |
| [Seeed reBot Web UI](https://github.com/Seeed-Projects/rebot_arm_webui/tree/06807f6dd3ed756ed77305490e6f2afed377f6fc) | 浏览器可视化和 IK 服务 | MIT；当前抓取链不需要新增前端，因此暂不引入 Node/Vite 运行栈 |

选择 LeRobot 的原因是它与现有 ACT 数据/训练环境共用一套七轴命名和公共角度定义，
本地固定提交已经含 B601-RS 驱动。这样无需再维护一份 CAN 协议实现，也不会把 ROS 2、
MoveIt 2、MuJoCo 和 Web 前端一起加入当前以 Python 为主的工程。

## 已接入的代码

`src/rebot_tea/hardware.py` 将 LeRobot 的 `{joint.pos: degree}` 接口适配为项目的固定七轴
弧度向量。真机目标仍由现有 `LimitedJointController` 生成五次轨迹，并继续检查：

- 七轴顺序和有限数值；
- 实测软限位、最大速度、最大加速度；
- 反馈时效、时间顺序、连续到位样本和总超时；
- LeRobot 是否返回完整七轴，以及驱动是否裁剪了目标；
- 异常终止后不继续发送轨迹，停止请求不宣称物理停止已确认。

固定提交的 MotorBridge `MotorState` 不含设备侧采样时间戳。适配器记录一次
`get_observation()` 开始读取时的单调时钟时间，在读取完成后检查年龄，因此
`feedback_age_limit_s` 能发现整组读取及控制循环延迟，却不能单独证明 CAN 帧的设备侧年龄。
首次现场验收仍须用丢帧/断线试验确认上游调用
能及时报错；在这项测试通过前，不能把软件时间戳当作物理反馈新鲜度证明。

LeRobot 只在真正连接时延迟导入。普通 `.venv` 可检查现场配置；连接和动作使用已经固定
LeRobot 的 `.venv-training`。没有从无许可证的 ROS 2 仓库复制源码。

## 现场配置门禁

把模板复制到 Git 已忽略的现场记录目录，而不是直接修改模板：

```bash
session=data/commissioning/首次接入日期
mkdir -p "$session"
cp config/hardware.template.json "$session/hardware.json"
```

填写 `hardware.json` 时，以下条件必须同时满足，否则适配器在导入 LeRobot、创建 CAN
控制器之前就失败：

1. `hardware_verified=true` 且 `control_enabled=true`；
2. 所有设备、固件、SocketCAN 通道、1–7 号电机、方向和零位已现场核对；
3. 校准记录和参数回读文件真实存在；相对路径从 `hardware.json` 所在目录解析；
4. 七轴软限位、速度、加速度、反馈时效、到位容差和运动超时均为实测值；
5. 固定提交的 RS 公共方向必须与实测一致，电机零位已经写入，不接受未实现的软件零偏；
6. `stop_behavior` 必须选择 `hold_position` 或 `disable_torque`，写明现场结果并设置
   `physically_verified=true`。失能可能使机械臂受重力下落，不能靠软件猜测。

其中映射字段的 JSON 形状如下；数值只能在现场核对后填写：

```json
{
  "motor_ids": {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_yaw": 5,
    "wrist_roll": 6,
    "gripper": 7
  },
  "motor_to_public_signs": [-1, -1, -1, -1, -1, -1, -1],
  "zero_offsets_rad": [0, 0, 0, 0, 0, 0, 0],
  "stop_behavior": {
    "mode": "hold_position",
    "physically_verified": true,
    "notes": "填写测试方法和结果"
  }
}
```

这组 ID、方向和零偏是当前固定 LeRobot RS 驱动唯一支持的映射，仍须逐轴实测；任何一项
不一致时门禁会拒绝运行，需要先修正适配实现，不能修改记录来迎合程序。
实测软限位还必须位于固定驱动的 RS 软限位以内；首次调试的额外硬上限为 `0.4 rad/s`、
`1.0 rad/s²`，反馈年龄上限不超过 `0.5 s`，到位容差不超过 `0.05 rad`，并要求至少三个
连续样本。这些上限只能通过代码审查后调整，现场配置不能放宽。

模板保留 `false` 和 `null`，因此当前状态不能运行真机。

## 两阶段运行

先只验证配置和目标。下面的七个角依次对应 `shoulder_pan、shoulder_lift、elbow_flex、
wrist_flex、wrist_yaw、wrist_roll、gripper`，单位为度：

```bash
bash scripts/control_linux.sh joint-plan \
  data/commissioning/首次接入日期/hardware.json \
  <当前肩旋角> <当前肩抬角> <当前肘角> <当前腕俯仰角> <当前腕偏航角> <当前腕滚转角> <当前夹爪角>
```

`joint-plan` 只解析配置、检查七轴目标和写报告，不导入 LeRobot、不连接 CAN、不使能电机。
模板或未完成记录会明确失败。

现场已经完成只读探测、官方校准、逐轴低速测试和停止测试后，才在支撑机械臂并清空工作区
的情况下运行：

```bash
bash scripts/control_linux.sh joint-run \
  data/commissioning/首次接入日期/hardware.json \
  <目标肩旋角> <目标肩抬角> <目标肘角> <目标腕俯仰角> <目标腕偏航角> <目标腕滚转角> <目标夹爪角>
```

`joint-run` 是真实运动入口：它用 `.venv-training` 创建官方 B601-RS follower，显式
`connect(calibrate=False)`，不在程序内写零；随后按配置的 50 Hz 循环下发受限轨迹，实际频率
须现场测量，不能由模拟结果保证。结果写入
`artifacts/joint-target.json`。报告中的停止始终是“已请求”，不能替代观察机械臂、急停或
断电后的现场确认。第一次把七个目标都填成官方界面显示的当前公共角度，只验证保持；随后
一次只改变一个关节，并使用步骤5已经验证的小角度。

这次接入完成的是“关节目标到官方驱动”的软件路径。笛卡尔抓取步骤仍未接到 IK、碰撞检查
和真机执行，ACT 策略也未开放自动回放。状态机、IK和碰撞检查可先离线开发，
见 [抓取准备检查](grasp-readiness.md)；连接实物执行须先通过单关节和短轨迹现场验收。
