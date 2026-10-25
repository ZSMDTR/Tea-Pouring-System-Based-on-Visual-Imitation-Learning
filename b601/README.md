# B601 机械臂本地工作台

本目录提供 B601 工作台源代码、配置模板与可重建的上游改动。GitHub 版本不含示范数据、模型权重、设备标定、真实相机序列号、数据库或运行日志。

## 源码组成

`bootstrap_sources.sh` 将以下项目检出到本目录，并应用 `patches/` 与 `overlays/`：

| 目录 | 上游与固定提交 | 本地改动 |
| --- | --- | --- |
| `openpi` | [Physical-Intelligence/openpi](https://github.com/Physical-Intelligence/openpi) `15a9616a00943ada6c20a0f158e3adb39df2ccac` | B601 策略、数据转换、训练入口、Orbax 兼容补丁 |
| `openpi_fullsave` | 同上游、同提交 | 保持原版，用于独立完整存档环境 |
| `lerobot` | [Seeed-Projects/lerobot](https://github.com/Seeed-Projects/lerobot) `fe078af31247a6144200abbfa3c2ce29d0062033` | Orbbec 图像裁剪、输出尺寸和等比补边 |
| `lerobot-robot-seeed-b601` | [Seeed-Projects/lerobot-robot-seeed-b601](https://github.com/Seeed-Projects/lerobot-robot-seeed-b601) `27b97166f49165c181bcf48db641324c81a37760` | 从臂观测形状修正 |

上游许可证文本保存在 `licenses/`。工作台 RTC 引用 PI 方法的 MIT 文本保存在 `b601_workbench/rtc/LICENSE-PI`。

## 初始化

需要 Git、Python 3.10、Python 3.11、可用的 CUDA/PyTorch 驱动，以及 B601 与相机的本机驱动。以下命令在本目录执行：

```bash
bash bootstrap_sources.sh
python3 init_local.py
B601_PYTHON310=/path/to/python3.10 B601_PYTHON311=/path/to/python3.11 bash setup_envs.sh
source activate_b601.sh
python3 b601_workbench/launch.py --no-browser
```

如两个 Python 版本已在 `PATH` 中，可省略 `B601_PYTHON310` 和 `B601_PYTHON311`。`setup_envs.sh` 会安装固定版本的独立环境；采集环境沿用原环境记录的三个 NumPy 元数据冲突，并拒绝新增依赖冲突。

`init_local.py` 从 `.example.json` 生成本机配置，不覆盖已有配置。请填写 `b601_workbench/capture_profile.json` 中的引导臂串口和两个相机序列号；模型、数据与标定应放在 `config.json` 所指的本地目录。`data/official_center_ready.json` 的标定和 URDF 哈希默认留空，待现场核对后填入；未核对时该待命位会拒绝执行。

数据目录需要 LeRobot 数据集，模型目录需要 π0.5 基础权重；这些文件不能从本仓库恢复。工作台启动后可通过只读状态页面核对解释器、相机、CAN、数据和模型。发送机械臂动作前仍需现场确认设备接线、标定与安全空间。

「官方手扶调姿」额外使用一个未附带许可证的 Seeed 上游提交；公开仓库不含其原文。仅在本地需要该模式时，按 [来源说明](b601_workbench/vendor/seeed_gravity/README.md)执行获取脚本。
