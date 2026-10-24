# 手柄遥操作开源实现复核

复核日期：2026-10-16。目标是 **Windows XInput 手柄 → WSL → B601-RS → 本地 LeRobot 数据集**。下面区分作者的实机声明、仿真测试和本项目实际验证；外部仓库的成功不能代替这台机械臂的通信、标定及停止验证。

| 实现与固定源码 | 型号、验证边界 | 接口与依赖 | 许可证及本项目选择 |
|---|---|---|---|
| [官方 LeRobot，`ae2b57f12223c8c5184fcd814eb63cd9ce7de203`](https://github.com/huggingface/lerobot/tree/ae2b57f12223c8c5184fcd814eb63cd9ce7de203)；本机版本 0.6.2 | 有 B601-RS follower；本项目尚未验证真实运动。官方通用 gamepad 并不是该型号的完整遥操作链路。 | gamepad 输出 `delta_x/y/z` 与夹爪枚举 `0=close, 1=stay, 2=open`；RS follower 接收七个 `joint.pos` 公共角度值。手柄输入使用 pygame/hidapi，本机机器人使用 Linux SocketCAN。 | [Apache-2.0](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/LICENSE)。保留已固定的 RS 驱动与原生数据格式，增加输入适配，避免为手柄升级整套依赖。 |
| [B601-DM ROS 2 gamepad，`7889a3acb8a36444ff0db0c88843b7496e690b84`](https://github.com/danieldoradotalaveron-rb/rebotarm_cartesian_gamepad_teleop_ros2/tree/7889a3acb8a36444ff0db0c88843b7496e690b84) | **B601-DM**；README 明确为 `dry_run` / RViz 仿真，尚不控制真实机械臂。 | ROS 2 Jazzy、joy、驱动 overlay、FK/IK；参考按住允动、约 0.3 秒输入超时和 IK 跳变检查。笛卡尔/运动学接口不能直接接成 RS 的七轴公共度。 | README/package 声明 Apache-2.0，但复核提交没有独立 LICENSE 文件。仅参考设计；不复制 DM 参数、驱动或整套 ROS 栈。 |
| [rebot-phone-teleop，`818d5ba9916feda6fd9342324a021cd1b3419f57`](https://github.com/ammarjmahmood/rebot-phone-teleop/tree/818d5ba9916feda6fd9342324a021cd1b3419f57) | **B601-RS**；作者称 iPhone 路线来自真实 RS 使用，拆出的包由模拟电机总线测试覆盖；HEBI/Quest 的硬件验证较少。本项目未复现。 | 手机/VR 位姿 → IK → 原生 RobStride CAN，不是普通游戏手柄或 LeRobot 采集器。Python 3.11、Pinocchio/MotorBridge；原生关节弧度及正向夹爪行程与 LeRobot 公共度/符号约定不同。 | [代码 MIT](https://github.com/ammarjmahmood/rebot-phone-teleop/blob/818d5ba9916feda6fd9342324a021cd1b3419f57/LICENSE)；RS URDF 为 CERN-OHL-W v2。参考按住允动、掉线停止和误差监视；不拷贝其电机增益、关节符号或夹爪参数。 |
| [XInput-Python，`e0aacedd540be156f83815b02590f22c8226779a`](https://github.com/Zuzu-Typ/XInput-Python/tree/e0aacedd540be156f83815b02590f22c8226779a) | Windows 手柄输入库，不包含机器人或运动验证；最新主分支提交为 2025-06-10。 | 标准库 ctypes 调用 XInput，读取按钮、摇杆、扳机及连接状态；没有 Windows→WSL 通信、机器人允动或采集功能。 | [Zlib](https://github.com/Zuzu-Typ/XInput-Python/blob/e0aacedd540be156f83815b02590f22c8226779a/LICENSE)。作为 API 参考；本项目直接依据微软公开 API 编写最小输入端，不复制上游实现、不增加运行依赖。 |

## 为什么只补最小接口

- **输入端留在 Windows**：现有手柄由 XInput 识别，WSL 当前缺少可用的 xpad 路径。读取已有 Windows 接口，不为输入桥安装 ROS、虚拟手柄或替换驱动。
- **跨系统只传归一化输入**：四根摇杆轴为 `[-1,1]`，两根扳机轴为 `[0,1]`。本地 TCP 一问一答，回显请求编号；4096 字节上限；WSL 用自己的单调时钟判断响应耗时及过期，不比较两个系统的时钟。
- **控制与记录分开**：WSL 关节点动层处理 LB 允动、摇杆死区、关节组和软件限位；硬件层沿用固定 RS 适配器。内部角度为弧度，只有硬件/数据边界转换为公共度；不把原始电机弧度混进数据集。
- **只记录成功发送的动作**：将状态、图像与 SDK 接受的同一个目标关联；SDK 若裁剪/改变目标，现有适配器报错停止，不能把未发送目标保存为成功动作。实测时间保存在 sidecar，原生 `frame_index/fps` 是名义时间网格，并非硬实时同步证明。
- **测试输入明确标记**：`--synthetic` 每帧为 `synthetic=true`，只用于无人值守离线验收，硬件入口拒绝它。软件停止请求、模拟成功和真实机械臂停止分别记录。

截至复核，本项目新增桥已通过标准库协议测试和真实本地 socket 测试；完整离线采集的结果应以实际运行报告为准。这些检查不能证明真实抓取、碰撞保护或倒茶成功。

## 直接参考

- [微软 XInputGetState 返回值与断连语义](https://learn.microsoft.com/windows/win32/api/xinput/nf-xinput-xinputgetstate)。
- [固定 LeRobot gamepad 接口](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/src/lerobot/teleoperators/gamepad/teleop_gamepad.py)。
- [固定 RS follower 的发送与单位边界](https://github.com/huggingface/lerobot/blob/ae2b57f12223c8c5184fcd814eb63cd9ce7de203/src/lerobot/robots/rebot_b601_follower/rebot_b601_follower.py)。
