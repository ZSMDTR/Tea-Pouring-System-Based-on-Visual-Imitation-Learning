# GitHub仓库与同步

目标：[Tea-Pouring-System-Based-on-Visual-Imitation-Learning](https://github.com/ZSMDTR/Tea-Pouring-System-Based-on-Visual-Imitation-Learning)。

仓库保持私有，默认分支main；Linux项目位于 /home/zsmdtr/projects/rebot-tea。
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

## 当前推送问题与处理（2026-10-14）

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
