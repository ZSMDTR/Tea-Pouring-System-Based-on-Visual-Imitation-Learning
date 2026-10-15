# B601-RS USB-CAN 型号查询

查询日期：2026-10-15。官方资料可确认套件使用 **PCAN-USB 兼容接口**，不能仅据接口名称
认定板卡为 PEAK 原厂产品；实际板卡品牌、硬件版本、固件和 VID/PID 仍需在实物上核对。

用户随后确认全部配件均由机械臂原厂配套提供，工程已记录此来源，按官方 PCAN-USB
路线完成软件准备。该确认解决了配件来源问题，现场只需读取对应设备的身份和运行参数。

## 官方依据与结论

| 来源 | 原文信息 | 可确认的内容 |
|---|---|---|
| [Seeed B601-RS 快速入门](https://wiki.seeedstudio.com/cn/rebot_b601_rs_getting_started/)第6步 | “套件里是 PCAN-USB”；使用 `peak_usb`；总线 1 Mbps | 当前官方套件按 PCAN-USB / Linux SocketCAN 路线使用 |
| 同页固件修复章节 | 固件名 `pcan_canable_hw.bin`；模块有 BOOT / 120R 开关 | 资料涉及 CANable 类板卡运行 PCAN 兼容固件的方案，不能把 PCAN 名称当作品牌证明 |
| [Seeed 官方 RS BOM](https://github.com/Seeed-Projects/reBot-DevArm/blob/main/hardware/reBot_B601_RS/README.md) | `CAN-USB driver board`，型号栏为空；采购链接指向预装 PCAN 固件的 USB-to-CAN 板（ASIN `B0GBW7RTXD`） | 公开 BOM 未公布唯一商品料号，参考采购项是 PCAN 固件转换板 |
| [Seeed RS 电机 SDK](https://wiki.seeedstudio.com/rebot_arm_b601_rs_mit_control/) | `CANABLE / PCAN-USB, etc.` | 其他供货批次或自行配置可能使用不同兼容板，需要读实物身份 |

因此现阶段准确名称是：**B601-RS 套件 USB-to-CAN 板，PCAN-USB 兼容固件/接口**。
不要以此把未验证的板卡写成 PEAK 某个具体产品编号，也不要用 DM 的 USB2CAN 串口桥配置
替代 RS 的 SocketCAN 通道。

## 到手后怎样确认实际型号

1. 记录板卡正反面丝印、包装标签与 BOOT/120R 开关，先只接电脑识别 USB。
2. Windows 设备管理器读取设备名、厂商与硬件 ID（VID/PID），保存到现场接入记录。
   项目 `scripts/control_windows.ps1 doctor` 可以只读查询候选设备。
3. 使用 WSL 时，附加已确认的那一块设备；Linux 中执行 `lsusb`、`ip -details link show`
   并记录实际 `can0/can1/...` 与对应驱动。PCAN 路径通常是 `peak_usb` 或已安装的
   PEAK `pcan` netdev 驱动。CANable 的具体驱动取决于固件，不能一概认定为 `gs_usb`。
4. 识别完成后才按首次连接文档配置 1 Mbps 并运行只读电机探测。未出现 CAN 接口时先检查
   USB、固件和驱动，不通过盲目刷固件或写电机 ID 来修复。

`config/hardware.template.json` 仅新增官方参考信息，`usb_can_model` 和 `usb_vid_pid`
继续为空，`actual_hardware_identity_verified=false`。USB-CAN 尚未在当前 WSL 枚举到。
