---
name: smartrf-diagnostics
description: 分析 SmartRF v4 的 `srf_debug -a`、分区诊断、协议栈事件日志以及 Host/Device 对照快照，覆盖链路状态、连接实现、配对、handler、队列、统计、连接参数控制、CQA、PHY 时序和寄存器。用于定位配对或连接失败、主动或异常断连、supervision、ISR late/skip、subinterval/F8K、参考时钟同步、device 相位跟踪、跳频恢复、队列溢出、丢包/重传、信道质量、低功耗和 PHY 配置异常。
---

# SmartRF Diagnostics

## 读取参考资料

- 分析完整 `srf_debug -a` 时，完整读取 [fields.md](references/fields.md)，不要跳过没有明显异常的分区。
- 判断根因、比较 Host/Device 或比较前后两份快照时，完整读取 [reasoning.md](references/reasoning.md)。
- 只收到单个分区时，读取 `fields.md` 的对应分区和 `reasoning.md` 的“证据规则”。

## 执行分析

1. 识别日志来源：SmartRF 版本、role、采集时刻、当前状态，以及快照是在运行中还是断开后取得。将 core 事件日志与快照分开处理。
2. 清点 `[link]`、`[connected_impl]`、`[pair_info]`、`[pending_pair_info]`、`[handlers]`、`[queues]`、`[statistics]`、`[ctrl]`、`[cqa]`、`[phy]`。明确缺失、裁剪、`compiled_out`、`unavailable` 和版本新增字段。
3. 先检查状态一致性，再检查 RF 时间和恢复状态，最后检查数据路径。不要从一个累计计数器直接推断当前正在失败。
4. 对累计计数器优先计算两份快照的增量和比率。只有一份快照时，说明它只能证明“历史上发生过”，不能给出发生频率或当前趋势。
5. 同时有 Host 和 Device 时，按连接参数、chanmap、当前/目标 hop、event、PID、收发统计和故障时间做对照。考虑两台设备的本地 timer 数值没有共同绝对时间基准。
6. 将结论分成：直接证据、强推断、待验证假设。至少给出一个最可能根因和支持它的跨分区证据，不罗列没有排序的可能性。
7. 给出最小补充采集建议：优先请求同一时刻两端 `srf_debug -a`、清零后固定时长的统计、故障前后事件日志或 DebugIO 波形。不要一开始就建议增加大量统计。

## 使用源码校准

当当前工作区包含 SmartRF v4 源码时，以以下文件为字段和语义权威来源：

- `subsys/wireless/smartrf_v4/src/srf_debug.c`
- `subsys/wireless/smartrf_v4/src/handler/connected/conn_impl_c/`
- `subsys/wireless/smartrf_v4/doc/`

遇到参考资料未覆盖的新字段时先检索打印点和字段写入点，再解释；不要仅凭名称猜测。不要修改代码，除非用户明确要求修复或增加诊断。

## 输出格式

按以下顺序组织结果：

1. 一句话结论与故障层级；
2. 时间线或当前状态；
3. 关键证据，关联至少两个分区；
4. 各分区异常与正常项；
5. 不确定性和被排除的原因；
6. 下一步最小验证动作。

保留关键数值及单位。对 wrap-around 的 16/32 位 event 或 timer 使用模运算语义，不把回绕后的负差直接解释成错误。
