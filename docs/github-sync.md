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

本次远程访问使用Windows Git已有的凭据管理器，未将访问令牌写入仓库。
当前非交互WSL会话不能直接执行Windows凭据管理器程序，未改动WSL全局互操作设置。
如果WSL终端尚未配置认证，可在PowerShell使用现有Windows Git推送Linux项目：

```powershell
git -c safe.directory=//wsl.localhost/Ubuntu-22.04/home/zsmdtr/projects/rebot-tea -C "\\wsl.localhost\Ubuntu-22.04\home\zsmdtr\projects\rebot-tea" push
```

在普通交互WSL会话配置凭据共享时，参见
[Microsoft官方说明](https://learn.microsoft.com/en-us/windows/wsl/tutorials/wsl-git)。
不需要把密码或访问令牌发到聊天中。

同步是“提交并推送”操作，不是文件每次保存就自动上传；
生成结果和真实视频不会因为执行git push而上传。
