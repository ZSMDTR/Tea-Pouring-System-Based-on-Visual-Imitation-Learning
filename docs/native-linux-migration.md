# WSL 项目迁移到原生 Linux

更新时间：2026-10-24。当前工作树仍在 WSL2；本文所述硬件检测只代表该 WSL 会话。

## 备份包

本次备份放在 Windows 共享目录：

```text
C:\Users\24734\Documents\rebot-tea-handoff-20261024\
```

里面应有：

- `rebot-tea-project-20261024.tar`：整个项目工作树与 `.git`，包含暂存、未暂存和未跟踪文件；排除 `.venv*`、`data/`、`outputs/`，后两项分别备份。
- `rebot-tea-data-20261024.tar`：全部当前 `data/`，包含合成数据和两路相机短采集帧；没有真实示教数据。
- `rebot-tea-outputs-20261024.tar`：全部 ACT 检查点。现有检查点来自合成数据，不是可抓壶策略。
- `lerobot-calibration-20261024.tar`：Arm102 的 `leader.json` 和 B601-RS 的 `follower.json`。
- `handoff-manifest-20261024.txt` 与 `SHA256SUMS`：来源提交、Git 状态、文件清单和校验值。

项目包保留当前 `.git` 历史与 dirty 工作树。此快照中本地 `main` 与 GitHub `main` 同为
`7ec8527`；新改动尚未提交或推送。解包后不要运行 `git clean`、`git reset` 或立即 `git pull`。

## 在原生 Linux 上恢复

将 Windows 共享分区或移动盘挂载后，找到上述目录并先验证哈希：

```bash
cd /path/to/rebot-tea-handoff-20261024
sha256sum -c SHA256SUMS
```

将项目恢复到 Linux 文件系统，不要直接在 Windows NTFS 路径运行训练：

```bash
mkdir -p ~/projects
tar -xf rebot-tea-project-20261024.tar -C ~/projects
tar -xf rebot-tea-data-20261024.tar -C ~/projects/rebot-tea
tar -xf rebot-tea-outputs-20261024.tar -C ~/projects/rebot-tea
mkdir -p ~/.cache/huggingface/lerobot/calibration
tar -xf lerobot-calibration-20261024.tar -C ~/.cache/huggingface/lerobot/calibration
cd ~/projects/rebot-tea
git status --short --branch
```

检查结果包含预期的 staged、unstaged 和 untracked 项；不要为了让状态变干净而丢弃它们。
之后可用 `code .` 打开项目。VS Code 应连接本机 Linux，并选择新建环境中的解释器。

## 原生 Linux 首次核对

先检查系统和设备，再创建环境：

```bash
python3 --version
nvidia-smi
lsusb
ip -br link
ls -l /dev/serial/by-id/ 2>/dev/null || true
```

当前 WSL 的参考值为 Python 3.10.12、RTX 5060 Laptop、驱动 573.24、`nvidia-smi` 显示
CUDA 12.8；设备以前在 WSL 中识别为 Gemini 305 `2bc5:0840`、Gemini 2 `2bc5:0670`、
CH340 和 candleLight `1d50:606f`。**原生 Linux 上的枚举结果才是新系统实际状态**；设备名、
串口路径和 CAN 接口名都要重新记录。需要确认相机两台同时出现、Arm102 串口存在、USB-CAN
驱动创建 SocketCAN 接口后，才继续软件接入。

系统 NVIDIA 驱动必须先由原生 Linux 的驱动安装流程配置，确认 `nvidia-smi` 工作后再建环境。
仓库没有独立的系统 CUDA Toolkit 安装脚本；CUDA 12.8 runtime 来自锁定的 PyTorch wheels。
新机环境入口为：

```bash
cd ~/projects/rebot-tea
bash scripts/bootstrap.sh
bash scripts/bootstrap_training.sh
bash scripts/bootstrap_camera.sh
```

如果只需先恢复主从控制，可先按控制依赖说明创建控制环境；相机 SDK 与训练环境可在驱动检查后分别安装。
不要复制旧 WSL 的 `.venv`、`.venv-training` 或 `.venv-kinematics` 来替代安装。

## 机械臂接续

双臂校准文件已备份，正常情况下直接复用 `leader` / `follower` ID，不重复校零。WSL 上最近的主从
启动被 5° 起始姿态检查拦截，未发送动作目标；最新误差读数见 `docs/HANDOFF.md`。恢复后先支撑从臂，
把主臂摆到从臂当前姿态，使所有对应读数相差小于 5°，再按
[Arm102 现场步骤](arm102-wsl-next-steps.md)做 10 秒、10 Hz 的逐轴小幅测试。
先确认方向、限位和独立停止方式，全部通过后再录示教数据。
