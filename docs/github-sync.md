# GitHub仓库与同步

目标：[Tea-Pouring-System-Based-on-Visual-Imitation-Learning](https://github.com/ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning)。

仓库当前为公开仓库，默认分支 main。下文 2026-10-14 至 2026-10-15 的路径与操作记录对应当时的 WSL 项目。
本次接续远端已有的初始README提交，不强制推送或重建远端历史。

## 上传内容

上传源码、配置示例、合成反馈fixture、测试、VS Code配置、依赖版本和文档。
.gitignore 排除虚拟环境、模型权重、下载的第三方代码、原始数据、生成结果和本地凭据。
换机时按README和kinematics.md重建环境，不从GitHub下载本机虚拟环境。

## 后续修改

在已认证的VS Code WSL终端中，可以使用Source Control提交并同步。
标准Git流程是：

```bash
git status
git add .
git commit -m "说明具体修改"
git push
```

## 历史推送问题与处理（2026-10-14）

当前WSL会话没有credential.helper、GitHub CLI或可用GitHub登录凭据。
普通HTTPS推送在认证阶段失败：`could not read Username ... terminal prompts disabled`。
Windows Git和Git Credential Manager文件存在，但本会话直接执行Windows程序报
`Exec format error`。进一步检查发现，经WSL内部`/init`转发可以运行Windows Git；
本轮已经使用现有GCM缓存认证成功读取远端main，无需新建令牌或修改全局互操作设置。
因此直接Linux Git的认证失败可通过现有Windows Git路线处理。

在Windows PowerShell中使用现有Git for Windows与GCM，推送同一个Linux仓库。
以下设置仅对这一次Git命令生效，首次需要时按浏览器提示登录有仓库写权限的账号：

```powershell
$rebotRepo = "\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea"
& "C:\Program Files\Git\cmd\git.exe" `
  -c "safe.directory=//wsl.localhost/Ubuntu-22.04/home/zsmdtr/projects/rebot-tea" `
  -c "credential.helper=" -c "credential.helper=manager" `
  -C $rebotRepo push origin main
```

在普通交互WSL会话配置凭据共享时，参见
[Microsoft官方说明](https://learn.microsoft.com/en-us/windows/wsl/tutorials/wsl-git)。
浏览器登录/GCM认证见 [GitHub官方说明](https://docs.github.com/en/get-started/git-basics/caching-your-github-credentials-in-git)。
普通WSL终端本身能执行Windows程序后，才能采用Windows凭据共享路线。
另一条路线是在WSL单独安装并交互登录GitHub CLI/GCM；本轮未安装或修改全局认证设置。

若认证后返回403，检查登录账号和仓库写权限；若返回non-fast-forward，先取回并核对远端改动，
再按正常合并流程同步，不强推。成功输出main到origin/main后，再检查分支同步状态。

同步是“提交并推送”操作，不是文件每次保存就自动上传；
生成结果和真实视频不会因为执行git push而上传。
本轮另提供 `docs/reports/prehardware-2026-10-14.json` 精简验收摘要，纳入Git；
完整本地日志、数据与检查点保持排除。

## 2026-10-15 同步内容

用户要求将目前全部工程成果同步到现有仓库。本轮包含此前尚未提交的 Gemini 2 SDK
适配和 LeRobot 相机插件、受限真机关节接口、反馈时间/超时修复、环境与 Notebook
检查脚本、VS Code 工作区整理、Word 实施文档、依赖锁定和现场准备说明。

当前阶段先做空茶壶抓稳、抬升、放回，尚未实现的完整抓放状态机、IK/碰撞衔接、茶壶
识别及标定工具，均在 [抓取准备检查](grasp-readiness.md) 明确列为待开发。
源码同步不表示实物抓取已完成。

本次提交前的完整验收记录保存为 `docs/reports/github-sync-2026-10-15.json`；
旧轮次的相机、环境和抓取检查摘要保留各自检查时刻与验证范围。
环境目录、第三方下载、训练数据/检查点、原始日志、相机查看器可执行文件和缓存
仍不纳入 Git；安装/重建入口、锁定版本和结果摘要随仓库保存。

实现内容提交为
[`f6c6d95`](https://github.com/ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning/commit/f6c6d95c05c95bd3c85380385a9cf6cdaeab6dfc)，
共61个文件。已通过现有Windows Git/GCM认证普通推送到main，随后独立执行
`ls-remote --heads origin main`，确认远端与本地均为该完整提交哈希，工作区干净。
核验时刻：2026-10-15 13:29:00 UTC。没有强推或更改仓库可见性。
此后补充的发布记录仅更新文档，并继续按普通提交/推送保存。

## 2026-10-25 原生 Linux B601 工作台

本次同步新增 [`b601/`](../b601/README.md)，保留此前 WSL 工程的目录与历史。公开内容包括工作台源码、配置模板、固定的上游版本、B601 补丁、环境重建脚本和测试。真实数据、模型权重、标定文件、设备序列号、本机数据库及运行状态不随仓库发布。

迁移后的本机工作台已完成三个 Python 环境重建、双相机预览、离线模型加载和 98 项工作台测试。未进行机械臂运动、数据采集或 Replay 实机验收。公开包的上游补丁已在对应固定提交上验证可应用；配置模板需要在新机器上填入实际设备和标定信息。
