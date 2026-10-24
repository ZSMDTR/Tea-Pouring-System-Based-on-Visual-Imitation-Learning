# 墨将 USB 手柄：Windows → WSL → 关节练习与示范记录

2026-10-16。代码位于 `/home/zsmdtr/projects/rebot-tea`，使用本机固定版本的
LeRobot 环境 `.venv-training`。不需要给手柄安装 Python 库，也不用把它透传到 WSL。
Windows 读取 XInput，WSL 通过本机 TCP 请求最新输入。两个窗口都要运行。

## 当前验收范围

- Windows 已实际识别当前手柄，读取六轴和常用按钮；品牌为用户提供的墨将，具体型号未知。
- Windows 到 WSL 的真实手柄输入桥已通过 2 秒检查，模拟后端执行 100 个保持周期；当时手柄中立，没有录示范。
- 自动模拟输入通过完整记录链：248 次模拟命令、96 个允动周期、1 段 76 帧示范，图像与七维状态/动作重新读取正常。
- 76 帧的平均实际间隔约 49.86 ms，最大约 60.30 ms。原生数据时间戳为 `frame_index/20`，实测时间另存；这不是硬实时同步。
- 未连接机械臂、未完成真实相机流验收、未采集真实抓壶数据。这些模拟数据只用来检查程序。

相关开源项目及选择理由见 [GitHub 项目比较](gamepad-open-source-review.md)。

## 先在模拟后端练习

把手柄 USB 线留在 Windows。打开 WSL 终端，执行：

```bash
cd /home/zsmdtr/projects/rebot-tea
bash scripts/gamepad.sh --backend mock --duration 120
```

等待出现 `LISTENING 127.0.0.1:8765`。这个命令只创建模拟机械臂与合成图像。
数据目录自动使用不同名字，禁止覆盖已有数据。

然后在 **Windows PowerShell** 执行：

```powershell
python -B "\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea\scripts\gamepad_windows.py" --connect 127.0.0.1:8765
```

启动时放开全部按钮、摇杆和扳机；程序最多等 30 秒。之后按住 LB 再动摇杆。
WSL 窗口每秒显示当前分组、允动状态和七个关节的反馈角度（度）。这里是数值模拟，尚无机械臂三维画面。
120 秒后会结束；也可在 WSL 窗口按 Ctrl+C。Windows 输入窗口会随服务端关闭而退出。

如只检查手柄，不向 WSL 发送输入，在 PowerShell 运行：

```powershell
python -B "\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea\scripts\gamepad_windows.py" --duration 10
```

## 按键与练习顺序

| 输入 | 功能 |
|---|---|
| 按住 LB | 允许运动；松开后锁定当时反馈位置作为保持目标 |
| 左摇杆左右 LX | 第 1 组 J1；第 2 组 J4 |
| 左摇杆上下 LY | 第 1 组 J2；第 2 组 J5 |
| 右摇杆上下 RY | 第 1 组 J3；第 2 组 J6 |
| RT − LT | J7 夹爪的正反关节方向；实物开合方向须现场确认 |
| RB 单击 | 在 LB 已松开、六轴中立时切换第 1/2 组 |
| A 单击 | 开始一段示范 |
| B 单击 | 保存当前段，人工标签 success |
| Y 单击 | 保存当前段，人工标签 failure |
| X 单击 | 丢弃当前未保存段 |

右摇杆左右 RX、方向键和其他按钮目前无控制功能。
J1–J7 顺序为 shoulder_pan、shoulder_lift、elbow_flex、wrist_flex、wrist_yaw、wrist_roll、gripper。

1. 先不录数据，按住 LB，每次只推动一根摇杆的一条轴；看对应关节数值变化，松 LB 后数值应停止变化。
2. 放开 LB 与全部轴，点 RB，重复练习 J4–J6。切换组时不要同时推摇杆。
3. 单练 LT/RT，再练两个关节的小幅组合。内部有 0.12 死区、速度/加速度和软限位；模拟限位不是实物限位。
4. 按 A，完成一小段动作，放开 LB，按 B 保存。按键按下产生一次事件，不需要持续长按。
5. 再录一段，用 X 丢弃，核对保存数量没增加。录制未完成就退出，该段也会被丢弃。

保存、开始或丢弃事件会先发送反馈保持目标并清除允动状态。
保存视频期间暂停控制计算，结束后需放开 LB 和全部轴，再重新按 LB。
输入失联、过期、反馈/相机异常或控制周期超过 100 ms 时结束当前会话；排查后重新启动。
软件停止请求不等于独立硬件急停；实机需要已验收的停止方式。

## 数据在哪里，能用来训练什么

WSL 终端启动时会打印数据完整目录，默认在 `data/gamepad/mock-时间戳/`。

- `data/`：LeRobot 原生表格，`observation.state` 与 `action` 都是七维**公共关节度**。
- `videos/`：RGB 视频，特征名 `observation.images.scene`。
- `recording/frames.jsonl`：输入序号、图像帧号、输入/关节/图像/发送的 WSL 单调时间戳。
- `recording/episodes.jsonl`：每段帧数和人工成功/失败标签。
- `recording/manifest.json`：synthetic 标志、单位和时间戳说明；`physical_task_verified` 始终为 false。
- 最近一次会话报告：`artifacts/gamepad-session.json`。

软件双视角检查可在模拟后端改用 `--camera synthetic-dual`。该模式需要同样的手柄输入桥和
LeRobot 训练环境，保存 `scene`、`wrist` 两路合成视频，并在写帧前检查两路主机时间戳、
帧号和可配置的 `--max-camera-skew-ms`（默认 50 ms）。记录中的时间差不是相机硬件同步
证明。实机入口仍限定单台 Gemini 2；真实双相机示教继续按
[双相机录制说明](dual-camera-recording.md)的 Arm102 路线进行。

动作只在后端报告目标发送成功后保存，记录最终提交的目标；驱动发生二次裁剪会报错停止。
失败示范保存了标签，**训练时仍须显式筛选 success；当前脚本没有自动筛选训练数据的功能**。
模拟后端和自动 `--synthetic` 输入的示范不能作为真实抓壶数据。
真实示范应先做固定位置空茶壶抓放，保证相机位置、场景、任务和开始状态一致。

## 实物启用前还需要完成

按 [第一次接入](robot-first-connection.md) 依次核对 USB-CAN/WSL 归属、只读扫描、零位、
参数回读、单关节低速和夹爪方向，再完成实际停止行为与 Gemini 2 图像流检查。
填写 [现场记录](commissioning-record.md) 和经过现场验证的硬件配置。
当前示例配置是未验收模板，程序会拒绝；不要直接把 verified 字段改为 true 来跳过检查。

硬件入口要求同时明确选择 `--backend hardware --execute --hardware-config` 和 `--camera gemini2`，
配置必须包含实际标定/参数记录和已经实测的 hold_position 停止方式；合成输入不能启用实机。
上游连接流程可能使能电机，因此这里先完成门禁与离线测试，当前没有执行实机入口。
此版本做关节遥操作；按茶壶末端的前后左右移动需要再接运动学与碰撞约束。
