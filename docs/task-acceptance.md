# 首版实物任务验收

本页为预先确定的研发标准，尚未进行实物验收。

## 空茶壶抓放

单次成功：抓稳空壶、完成预先记录的抬升、放回指定区域，全程没有掉落、碰撞或人工干预。
记录固定场景和未见摆位，提前划定放置区域、抬升目标和实际允许工作区。
20 次独立尝试至少 18 次成功；失败也计入总尝试，不删除失败来凑成功次数。

复制 docs/templates/grasp-trials.csv 到本地 data/acceptance/，逐行记录 20 次试验。
布尔观察必须填 true/false，留空不会被当作成功。

```bash
.venv/bin/python scripts/summarize_acceptance.py grasp \
  data/acceptance/grasp.csv --output artifacts/grasp-acceptance.json
```

## 冷水倒茶

先验证固定杯倾倒，再验证随机位置定位与跟踪，最后验证水量闭环。
每个杯子先独立标定有效容量，目标为容量90%，首版允许最终水量在85%–95%之间。
测量使用独立于控制器估计的参考方法，并记录方法、分辨率和容量标定版本。
若采用称重换算，记录皮重和用于换算的密度依据；不要将质量直接作为任意液体的体积。

复制 docs/templates/pour-trials.csv。capacity_ml 和 final_volume_ml 为独立测量值；
停止请求和停流时刻使用同一时钟，记录实际余流和停止延迟。
measured_volume_at_stop_ml 无法测量时留空，报告余流量为 null，不伪造零。
spill=true 或最终水量超出范围的试验判定失败；没有记录不能通过。
脚本汇总已录入试验，不自动决定全任务覆盖足够，也不独立确认物理事实。

```bash
.venv/bin/python scripts/summarize_acceptance.py pour \
  data/acceptance/pour.csv --output artifacts/pour-acceptance.json
```

当前离线 PourController 使用合成观测达到阈值时请求停止，尚未建模真实余流。
现场需要测量反馈/推理/执行延迟及余流后决定提前停止策略，反馈传感方案仍待验证。
抓放策略、检测效果和最终水量分别统计，训练损失或离线通过不替代任何一项实物结果。
