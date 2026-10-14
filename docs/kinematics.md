# B601-RS 官方模型与离线运动学

已在 WSL Ubuntu 22.04、CPython 3.10 下验证；不需要机械臂、相机、CAN、ROS 或 GPU。
该检查只导入 NumPy 和 Pinocchio，并在内存中加载模型，不导入官方 actuator 模块。

## 已安装内容

- Linux 项目：`/home/zsmdtr/projects/rebot-tea`。
- 独立环境：`.venv-kinematics`，与视觉开发的 `.venv` 分开。
- 官方源码：`vendor/reBotArm_control_py`，约 134 MB。
- 官方仓库：<https://github.com/Seeed-Projects/reBotArm_control_py>。
- 已核对提交：`6415d43130d1e143c70dc106096a857ac5556f81`。
- RS URDF：`vendor/reBotArm_control_py/urdf/RS/urdf/ReBot_Arm_RS.urdf`。
- 配套资源：`vendor/reBotArm_control_py/urdf/RS/meshes/`，应与 URDF 一起保留。
- 末端坐标系：`gripper_end`。真实夹持接触点或装上工具后的 TCP 仍需测量。

官方仓库当前的 `config/rebotarm.yaml` 已指向 `rebotarm_rs.yaml`，无需改成 DM。
本项目的离线检查直接加载 RS URDF，不依赖默认硬件配置，也不会实例化 `RebotArm`。
本次没有运行官方硬件示例、开启 CAN 接口或发送任何电机命令。

## 运行

在 VS Code 的 WSL 终端中执行：

```bash
cd ~/projects/rebot-tea
.venv-kinematics/bin/python scripts/check_kinematics.py \
  --output artifacts/kinematics-check.json
```

脚本加载可视与碰撞网格，计算零位和一个限位内非零构型的正运动学，
将六个机械臂关节的解析位置雅可比与 FK 有限差分比较，
再从邻近初值求解六轴完整位姿 IK，回到该非零构型的 FK 位姿。
失败会返回非零退出码；成功会输出 `"status": "PASS"`。

已验证结果：

| 项目 | 结果 |
| --- | --- |
| Pinocchio | 3.9.0 |
| NumPy | 2.2.6 |
| 机械臂关节 | `joint1` 至 `joint6` |
| URDF 配置/速度维数 | `nq=8, nv=8` |
| 额外 URDF 关节 | `gripper_joint1`, `gripper_joint2` |
| 可视/碰撞几何数量 | 25 / 10 |
| 零位末端位置，米 | `[0.30171017, 0.00000026, 0.21774671]` |
| 非零构型末端位置，米 | `[0.32326750, -0.02448656, 0.43391130]` |
| 雅可比最大差分误差 | `2.3746e-8` |
| 六轴位姿 IK 回环 | 25 次迭代收敛，所有迭代满足 URDF 关节限位 |
| IK 最终位置误差 | `1.4320e-9 m` |
| IK 最终姿态误差 | `5.0139e-9 rad` |
| `pip check` | 无依赖冲突 |

URDF 中的两个夹指关节不意味着实物有两个夹爪电机。
官方 RS 配置包含一个 ID `0x07` 夹爪电机；电机角度到夹指位移的映射仍需按实物确认。
这里固定夹指模型关节为零，仅验证六轴机械臂运动学。

IK 是检查脚本内的简洁阻尼最小二乘计算，并未安装或调用完整硬件 SDK。
目标完整位姿由 `q=[0.2, 0.7, 1.0, -0.3, 0.2, 0.1] rad` 的 FK 生成，
初值为 `[0.25, 0.66, 1.03, -0.26, 0.15, 0.16] rad`，不预设真实抓取姿态或 TCP。
它证明一个限位内目标能从附近初值求解，不代表任意相机目标均可达或任意初值均收敛。

## 重建独立环境

现有设备已经安装完成，无需重复执行。换机时在 Linux 项目目录运行：

```bash
mkdir -p vendor
git clone https://github.com/Seeed-Projects/reBotArm_control_py.git vendor/reBotArm_control_py
git -C vendor/reBotArm_control_py checkout 6415d43130d1e143c70dc106096a857ac5556f81
python3.10 -m venv .venv-kinematics
.venv-kinematics/bin/python -m pip install -r requirements/kinematics.txt
.venv-kinematics/bin/python -m pip check
.venv-kinematics/bin/python scripts/check_kinematics.py
```

`requirements/kinematics.txt` 锁定已验证的依赖版本。不要在这个环境直接运行未约束的
`pip install --upgrade pin`：Pinocchio 3.9.0 的宽松依赖会允许安装更新的 urdfdom / tinyxml2，
本次实际触发了 `liburdfdom_sensor.so.4.0` 和 `libtinyxml2.so.10` 缺失。
已通过 `cmeel-urdfdom==4.0.1`、`cmeel-tinyxml2==10.0.0` 修复，无需修改系统动态库。

## 验证范围与后续

以上通过证明：原生计算依赖能加载、RS 模型和网格完整、六轴 FK 与位置雅可比相符，
一个已知可达的完整位姿能从附近初值通过 IK 收敛且不越过关节限位。
它不证明任意抓取目标可达、任意初值 IK 可收敛、路径无碰撞、动力学参数准确或真实零位一致。
官方该提交中未提供 SRDF 或可直接使用的自碰撞对排除配置，因此未自行编造相邻连杆排除规则，
报告明确标记 `self_collision_checked=false`，没有宣称该测试位姿无碰撞。
碰撞网格加载不等于已经进行自碰撞检测或路径规划；尚未将离线示例的笛卡尔抓取步骤转换成实机轨迹。

拿到实物后需测量底座/桌面/相机外参、末端 TCP、夹爪开度映射和软限位，
再验证 IK、碰撞约束及速度/加速度限制。官方驱动和控制示例依赖 MotorBridge，
未因本运动学检查而安装或启用硬件控制运行链。
