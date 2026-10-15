# 项目入口与文件导航

## 应该打开哪个工作区

真正的项目仓库只有一个：

```text
/home/zsmdtr/projects/rebot-tea
```

日常开发统一打开仓库根目录中的 `reBot-B601-RS.code-workspace`：

```bash
wsl -d Ubuntu-22.04
code ~/projects/rebot-tea/reBot-B601-RS.code-workspace
```

也可以在 Windows 文件资源管理器中打开：

```text
\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea\reBot-B601-RS.code-workspace
```

Windows 桌面旧文件 `reBot-Codex-WSL.code-workspace` 也是一个 VS Code 快捷入口，它指向
同一个 `/home/zsmdtr/projects/rebot-tea`。`Codex-WSL` 描述的是开发方式：在 Windows 上使用
VS Code/Codex，通过 WSL 运行 Linux 工具；`B601-RS` 描述的是项目所控制的机械臂型号。
它们不是两套代码，也不需要同步文件。

`/home/zsmdtr/rebot-workspace` 只是早期创建的工作区启动目录，不是 Git 仓库，不保存项目
代码。其工作区快捷方式也已改为指向真实仓库。

打开正确后，VS Code 资源管理器最上层应显示 `reBot Tea · B601-RS`，集成终端执行
`pwd` 应得到 `/home/zsmdtr/projects/rebot-tea`，左下角应显示 WSL Ubuntu-22.04。

## 根目录结构

```text
rebot-tea/
├── README.md                     项目总览和常用命令
├── reBot-B601-RS.code-workspace  推荐的 VS Code 入口
├── pyproject.toml                Python 包定义
├── src/rebot_tea/                项目核心代码
├── scripts/                      安装、检查、控制、训练脚本
├── config/                       模拟配置和真机配置模板
├── tests/                        自动测试
├── docs/                         操作说明、现场记录和验收文档
├── requirements/                 各环境依赖及锁定版本
├── fixtures/                     小型离线测试输入
├── vendor/                       固定版本的 LeRobot 和官方机械臂 SDK
├── data/                         本地数据集与现场记录，不上传 Git
├── outputs/                      本地训练输出和检查点，不上传 Git
├── artifacts/                    本地检查报告和可视化，不上传 Git
├── models/                       本地模型权重，不上传 Git
├── .venv/                        主开发环境
├── .venv-training/               LeRobot/ACT 训练及真机适配环境
└── .venv-kinematics/             Pinocchio 运动学环境
```

VS Code 默认隐藏三个虚拟环境、Python 缓存和 Ruff 缓存，避免资源管理器被数万个依赖文件
占满。`vendor、data、outputs` 仍可在资源管理器中查看，但默认不参与全局搜索和文件监听。

## 核心代码怎么找

| 目的 | 文件 |
|---|---|
| 模拟抓取主入口 | `src/rebot_tea/__main__.py` |
| 视觉检测 | `src/rebot_tea/vision.py` |
| Gemini 2 RGB-D 采集与标定信息导出 | `src/rebot_tea/orbbec.py`、`scripts/camera.sh` |
| LeRobot Gemini 2 相机插件 | `src/rebot_tea/lerobot_orbbec.py` |
| RGB-D 几何和坐标变换 | `src/rebot_tea/geometry.py` |
| 抓取步骤规划 | `src/rebot_tea/planning.py` |
| MockRobot | `src/rebot_tea/robot.py` |
| 七轴轨迹、限位和模拟后端 | `src/rebot_tea/joints.py` |
| LeRobot B601-RS 真机适配 | `src/rebot_tea/hardware.py` |
| 只读电机探测 | `src/rebot_tea/connection.py` |
| 数据契约 | `src/rebot_tea/dataset_contract.py` |
| 倒茶反馈状态机 | `src/rebot_tea/pouring.py` |
| 任务验收统计 | `src/rebot_tea/acceptance.py` |

## 工作时使用哪个环境

| 工作 | 使用环境 | 推荐入口 |
|---|---|---|
| 编辑代码、运行普通测试、模拟抓取、视觉 | `.venv` | `bash scripts/verify_all.sh` |
| ACT 数据、训练、LeRobot、受限真机关节入口 | `.venv-training` | `bash scripts/training.sh ...` 或 `scripts/control_linux.sh joint-run ...` |
| Pinocchio FK/IK | `.venv-kinematics` | `.venv-kinematics/bin/python scripts/check_kinematics.py` |
| Windows 原生 PCAN 备选 | Windows `.venv-control-win` | `scripts/control_windows.ps1` |

平时 VS Code Python 解释器保持 `.venv/bin/python`。训练和运动学脚本已经显式选择各自环境，
不需要频繁切换编辑器解释器。

Notebook 右上角选择 `reBot Tea - Python 3.10` 或 `reBot ACT - Python 3.12 CUDA`；
两个内核均已注册并执行 GPU 检查。环境/插件复核使用 `bash scripts/audit_software.sh`，
完整记录见 [软件环境检查](software-environment-check.md)。

## 文档阅读顺序

1. `README.md`：当前能力与常用命令。
2. `docs/robot-first-connection.md`：第一次连接机械臂。
3. `docs/open-source-control-integration.md`：受限真机控制入口及配置门禁。
4. `docs/after-hardware-workflow.md`：从接线到完成抓取的现场顺序。
5. `docs/training-start.md`：相机、示教、真实数据和 ACT 训练。
6. `docs/HANDOFF.md`：历史实施记录和新会话交接。

不要直接编辑 `.venv*、vendor、data、outputs、artifacts、models` 中的生成内容来修改项目
逻辑。代码修改放在 `src、scripts、config、tests`，说明修改放在 `README.md` 或 `docs`。
