# 无硬件开发与验收

主项目：/home/zsmdtr/projects/rebot-tea。VS Code 应连接 WSL: Ubuntu-22.04。
使用 .venv/bin/python 运行视觉流程；.venv-kinematics 专用于 Pinocchio。
两套环境隔离，避免系统 ROS、视觉和原生运动学库互相覆盖依赖。

## 已配置的软件用途

- Python 3.10 + NumPy + OpenCV headless：数据读取、深度处理、标定转换。
- PyTorch 2.10 + TorchVision 0.25：SSDLite MobileNetV3 COCO 基线。
- CUDA 12.8 运行库随 PyTorch wheel 安装；无需额外安装全局 CUDA toolkit。
- MotorBridge：安装官方 SDK，为后续适配准备，不运行电机控制或网关。
- debugpy / Ruff / unittest：调试、静态检查与回归测试。
- Matplotlib：导出任务点图；它不是机械臂动力学或碰撞仿真。
- Pinocchio 独立环境：官方 RS 模型加载和运动学数学验证。
- ROS Humble：保留系统安装，独立检查；当前流程无 ROS 依赖。

## 一次性初始化和完整验收

```bash
cd ~/projects/rebot-tea
bash scripts/bootstrap.sh
bash scripts/verify_all.sh
```

bootstrap 会下载 GPU 依赖及约14MB官方预训练权重。
完整已验收版本保存在 requirements/lock-linux-cu128.txt；独立运动学环境重建见
docs/kinematics.md。主环境初始化不会覆盖系统 Python 或 ROS。
完整验收包含真实 CPU/GPU 模型推理及一轮合成训练反向传播，
仅证明执行环境可用，不能证明实际识别准确率或训练效果。

## 检测一张图片

```bash
source .venv/bin/activate
python -m rebot_tea.vision --image /path/to/cup.jpg --label cup --device cuda
```

输出 artifacts/detections.json 和同名标注 PNG。未找到目标时输出空列表，
不会伪造目标或触发抓取。模型来自 TorchVision 官方 COCO 权重；可以检测 cup 类，
但茶壶、杯把抓点、夹持姿态和你实际场景的性能需要独立数据验证。
默认0.5阈值只是视觉演示；回放抓取使用 config/simulation.json 中的0.8阈值。

预训练权重首次由 scripts/prepare_model.py 显式下载并校验文件名哈希，
检测入口只读本地权重，不在推理时偷偷下载。

## 离线 RGB-D 回放

```bash
python scripts/generate_demo.py
python -m rebot_tea.replay
python scripts/plot_replay.py
```

演示 scene 声明 synthetic=true，框由合成 fixture 给定；不声称神经网络识别出了绘制方块。
结果应定位到 base (0.3, 0.0, 0.1)m，再生成5个模拟步骤。
artifacts/replay.json 明确写入 physical_grasp_verified=false。

真实录制数据的 scene 格式参考 fixtures/rgbd_scene.json：

- color：彩色图像路径，相对 scene 文件；必须已去畸变。
- depth：同分辨率 .npy 深度图，allow_pickle=false，已对齐 color。
- calibration：标定 JSON 的相对路径；格式见 fixtures/calibration.demo.json。
- synthetic=false：真实输入必须设 false，并使用 --detector ssdlite。
- T_base_camera：相机到基座的右手刚体变换；深度原值乘 depth_scale_m 得到米。

```bash
python -m rebot_tea.replay --scene /path/to/scene.json --detector ssdlite --device cuda
```

该命令仍只执行 MockRobot。缺少目标、无效深度、非法变换和目标越界都会终止。
深度算法目前取检测框中心5x5区域中有效值的中位数，是定位基线：
杯口中心可能看见杯底或背景，需要后续分割、抓点选择及TCP偏移才能用于实物。

## 运行与修改测试

```bash
.venv/bin/ruff check src scripts tests
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_environment.py --profile gpu
.venv-kinematics/bin/python scripts/check_kinematics.py --output artifacts/kinematics-check.json
```

VS Code 的 launch.json 提供模拟抓取、RGB-D回放与图片路径输入的检测调试。
tests 覆盖已知几何解、深度空洞、框边界、非法标定、工作空间、无目标及 mock限制。

## 后续开发边界

软件可以继续处理录制数据；相机驱动、真实内外参、夹爪TCP、机械零位、碰撞几何、
抓取成功反馈及实物控制时序必须有设备才能验证。
不自动运行上游的硬件演示；某些上游 --dry-run 仍会初始化机械臂。

参考：[TorchVision SSDLite](https://docs.pytorch.org/vision/main/models/generated/torchvision.models.detection.ssdlite320_mobilenet_v3_large.html)、
[PyTorch 版本组合](https://pytorch.org/get-started/previous-versions/)。
