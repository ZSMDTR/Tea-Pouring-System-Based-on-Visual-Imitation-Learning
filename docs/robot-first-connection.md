# B601-RS：电脑第一次连接与控制

当前优先验证机械臂通信、校准和手动控制，不需要相机、茶杯或学习模型。
已有的视觉、运动学和倒茶离线代码继续保留。

## 本机已经准备和实际未完成的部分

- WSL Ubuntu22.04：MotorBridge0.5.6、ip、usbutils、can-utils，存在peak_usb和gs_usb内核模块。
- Windows：独立 .venv-control-win，MotorBridge0.5.6原生ABI和网关程序可加载。
- 只读doctor、1–7电机探测/位置读取，以及官方Web控制网关启动脚本已提供。
- 当前未识别到接入的USB-CAN；COM9/COM10是蓝牙串口。
- Windows未检测到PCANBasic运行库，usbipd-win也未安装。
- 用户尚未确认零位/参数，因此没有自动写零、改ID、使能或发送运动指令。

这意味着程序入口已准备，但不能宣称当前电脑插线后必然可立即运动；
仍须按实际适配器补驱动、确认接线，完成一次实机验收。

## 1. 先确认适配器，不猜COM号

插入套件的USB-CAN后，Windows项目根目录运行：

```powershell
.\scripts\control_windows.ps1 doctor
```

报告写入artifacts/control-windows.json。没有CAN或驱动时返回非零是预期结果。
它只读USB和软件状态，不创建电机控制器。检测到候选不等于协议已验证。

若是PEAK PCAN，可使用Windows原生控制，但须安装厂商PCAN驱动和PCAN-Basic；
MotorBridge当前将Windows原生CAN支持标为实验功能。
若是CANable等设备，先按其实际固件和Linux驱动验证SocketCAN，不能把COM串口强行作为RS接口。
RS不是Damiao串口桥，不能用DM串口方案代替。

## 2. 选择一条运行路径

### WSL/Linux开发路径

项目根目录为 ~/projects/rebot-tea：

本机已有环境。若在新Linux机器只做控制，可先安装最小依赖，无须下载视觉GPU栈：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . -r requirements/robot-sdk.txt
```

```bash
cd ~/projects/rebot-tea
bash scripts/control_linux.sh doctor
```

若USB设备目前由Windows持有，WSL不会自动获得它。按
[Microsoft USB说明](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
安装usbipd-win并完成绑定/附加；首次安装或绑定需要Windows管理员操作。
不要同时让Windows网关和WSL程序占用同一适配器。

实物到位后，可按以下顺序操作；BUSID必须从usbipd list核对，不能照抄他人的编号：

```powershell
# 管理员PowerShell：安装和首次共享
winget install --interactive --exact --id dorssel.usbipd-win
usbipd list
$canBusId = Read-Host '输入已确认USB-CAN的BUSID'
usbipd bind --busid $canBusId

# 打开Ubuntu使其保持运行后，普通PowerShell中附加
usbipd attach --wsl --busid $canBusId
```

若换了PowerShell窗口，需要重新设置$canBusId。不要在Windows原生网关运行时附加设备。
切回Windows控制前，先停止Linux网关，再执行usbipd detach --busid $canBusId。
进入WSL后用lsusb和ip -br link确认；PCAN需要peak_usb，CANable通常需要匹配固件及gs_usb。

仅在确认适配器已附加、驱动已加载并出现can0后执行：

```bash
bash scripts/control_linux.sh can-up can0
bash scripts/control_linux.sh doctor can0
bash scripts/control_linux.sh probe can0
bash scripts/control_linux.sh gateway can0
```

can-up只配置已有CAN网络接口为1Mbps，使用sudo，不写电机参数。
Linux通道是can0，不带@1000000；can1等实际接口须相应替换。
如果根本没有can接口，先修复USB透传和适配器驱动，不能用修改电机ID解决。

### Windows原生PCAN备选路径

在Windows中克隆/打开本仓库，首次运行：

```powershell
.\scripts\setup_control_windows.ps1
.\scripts\control_windows.ps1 doctor
```

仅当实际适配器为PCAN且对应运行库/驱动已安装后：

```powershell
.\scripts\control_windows.ps1 probe -Channel can0@1000000
.\scripts\control_windows.ps1 gateway -Channel can0@1000000
```

can0在Windows映射第一个PCAN USB通道，不是Linux网络接口；多适配器时需核对顺序。
当前已验证的软件环境在本聊天的Windows工作区，换目录克隆后需重新运行setup。
该环境只装控制依赖，不安装GPU视觉栈，不要求Windows启动WSL。

## 3. 探测结果怎么看

probe使用RobStride Ping及有超时的0x7019位置读取：
- 1–3号按rs-06注册，4–7号按rs-00注册，host ID为0xFD；型号是配置提示，不是自动鉴定。
- 7个电机必须都响应，且新鲜位置有效，整体才判定通过。
- 每个位置附读取时刻，弧度单位；超时/缺失不会被伪装成零。
- 结果写artifacts/motors-linux.json或motors-windows.json。

探测会发送查询帧，但不使能、不校零、不写参数、不运动，也不在退出时默认失能。
它不是运动控制循环，不提供保持重力的能力。
部分RS固件在使能前可能无法稳定读取0x7019；若Ping已响应而位置读取超时，
先通过官方界面核对固件/状态，不据此盲目改ID或写零。本项目探测不会为读位置自动使能。

## 4. 打开官方控制界面并完成首次调试

网关仅监听127.0.0.1:9002。打开
[MotorBridge Studio](https://motorbridge.github.io/motorbridge-studio/)，
连接ws://127.0.0.1:9002，型号选择rebot-arm-robstride。
这里的UI会提供真实控制能力，点击使能、写零和运动会作用于实物。

按[Seeed首次运行说明](https://wiki.seeedstudio.com/cn/rebot_b601_rs_getting_started/)：

1. 固定底座并按套件要求接电源/CAN；连接前断电，不热插拔电机电源接口。
2. 扫描1–7号电机；成品臂不要盲目重写ID。
3. 将机械臂摆到厂家规定的零位姿态后校零；“写零”不是“回零”。
4. 选择RS默认电机参数模板，写入后回读核对，保存导出的参数。
5. 先单关节、低速、小角度，再检查夹爪和多关节；确认反馈和停止行为。
6. 保存实际适配器、通道、零位、参数、关节方向及工具偏移，后续复用。

当前项目没有把失能称为保持当前位置：失能会取消主动支撑，须根据实际台架支撑机械臂。
不自动调用上游示例2/4/7/8作为初连测试；这些示例含写零、使能或初始零目标，退出也可能驱动回零。
程序化连续轨迹、夹持和策略控制须在此验收后再实现/验证，而非直接把mock步骤下发到电机。

## 通过标准

- 设备型号和驱动明确；7电机在线，新鲜位置可重复读取。
- 校零与参数回读完成；每个关节方向正确，单关节小步运动可控。
- 明确保持/失能/急停/断电后的实际行为；再进入示教训练。

[MotorBridge平台与接口说明](https://github.com/motorbridge/motorbridge)。
