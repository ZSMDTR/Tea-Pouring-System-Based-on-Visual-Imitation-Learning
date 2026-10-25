# Seeed 重力控制来源

本功能使用 Seeed-Projects 的 [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py) 固定提交 `6415d43130d1`。该提交未附带 LICENSE 文件，因此公开仓库仅保存来源和 SHA-256 清单，不再分发上游源码。需要使用「官方手扶调姿」时，在本机运行：

```bash
python3 scripts/install_seeed_gravity.py
```

脚本下载固定提交、校验 `SOURCE.json` 里的三个文件哈希，并从上游 AST 生成本地 `control_law.py`。生成文件被 `.gitignore` 排除。工作台的 `official_gravity.py` 仅在进入该模式时加载此模块；其他模式不依赖它。
