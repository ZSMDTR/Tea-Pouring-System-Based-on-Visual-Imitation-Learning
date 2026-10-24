# 合成空壶抓放状态机

`config/grasp_cycle.synthetic.json` 定义明确标记为 synthetic 的 base 坐标和演示工作区。
`src/rebot_tea/grasp_cycle.py` 在开始前检查抓取点、放置点和全部示例路径边界，只产生
mock 语义命令：张爪、接近、下降、闭爪、抬升、移回、下降、释放、撤退。
它不求 IK、不检查碰撞，也不连接相机、CAN 或机械臂。

每条命令必须由外部反馈报告完成；闭爪完成后还必须单独提供抓持确认证据，才会发出
抬升请求。释放命令完成后同样要单独确认释放，才会请求撤退。反馈包含对应动作、
观测时间和当前时间。时间无效、倒退、过期、动作不匹配、失败或超时都会进入不可恢复的
`aborted` 状态，不再发出后续请求。确认值只接受真正的布尔值；闭爪成功不等于抓稳。

离线演示读取独立的合成反馈文件：

```bash
.venv/bin/python scripts/run_grasp_cycle_demo.py
.venv/bin/python -m unittest tests.test_grasp_cycle -v
```

演示报告写入 `artifacts/grasp-cycle.json`，即使流程完成也会标明
`physical_grasp_verified=false`、`physical_release_verified=false`、
`ik_verified=false` 与 `collision_checked=false`。它只验证事件顺序和故障锁存。
真实位姿、抓持信号、机械臂限位及停止行为仍须现场确定；不能把此演示当作实物抓放。
