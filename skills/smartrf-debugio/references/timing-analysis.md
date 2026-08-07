# 无线 DebugIO 时序分析参考

本文档已根据当前需求配置四类射频硬件信号。物理通道编号、双设备分组和黄金阈值仍需从每次 capture 或后续 `golden.dsl` 获得。

## 目录

- [观测模型](#观测模型)
- [信号字典](#信号字典)
- [通道角色识别](#通道角色识别)
- [事务状态机](#事务状态机)
- [指标字典](#指标字典)
- [采样分辨率](#采样分辨率)
- [异常与缺失信号](#异常与缺失信号)
- [CPU 与包长边界](#cpu-与包长边界)

## 观测模型

为每个结论标注：

1. `observed`：逻辑分析仪直接采到的 GPIO 电平、边沿或 sample index；
2. `proxy`：用户确认由射频硬件直接输出，可代表 TX/RX/sync 状态；
3. `inferred`：依据包含关系、时序顺序或跨设备相关性推断出的角色/事务。

GPIO 边沿是 observed，TX/RX/sync 语义是已配置的硬件 proxy。不要把 sync pulse 擅自命名为 preamble、SFD 或其他未确认的 PHY 精确边界。

## 信号字典

所有信号 idle 为低，且来自射频硬件直接输出：

| 角色 | 编码 | 上升沿 | 下降沿 | 高电平/脉冲含义 | 证据 |
| --- | --- | --- | --- | --- | --- |
| `txen` | level | TX 开始 | TX 结束 | TX 持续时间 | observed + proxy |
| `rxen` | level | RX 开始 | RX 结束 | RX 活跃时间 | observed + proxy |
| `sync_window` | level | sync window 开始 | sync window 结束 | RX 内的同步搜索窗口 | observed + proxy |
| `sync_pulse` | pulse | 同步到空中包 | 不单独定义 | 约 50 ns；宽度无业务指标意义 | observed + proxy |

典型 RX 关系：

- `rxen` 上升后经历约 5–50 µs ramp-up，再出现 `sync_window` 上升；该范围是需求中的经验描述，不是 pass/fail 阈值。
- 成功同步时，`sync_window` 内出现 `sync_pulse`，随后 window 结束；`rxen` 继续到剩余 payload 接收完成。
- `sync_pulse` 只证明同步 marker 被观察到；没有 packet-done、CRC 或上层 marker 时，不进一步声称 payload、CRC 或业务接收成功。
- 未同步或受干扰时，可能始终没有 `sync_pulse`；rxtov 后 `sync_window` 与 `rxen` 几乎同时下降。“几乎同时”的容差尚未定义，只能标为候选 timeout。

## 通道角色识别

最多处理两个设备、每设备四路，共八个物理通道；实际 capture 可以只有 TXEN/RXEN 或缺少部分 sync 信号。

按以下优先级识别：

1. 使用用户提供的 `physical channel -> device -> signal` 映射，置信度 `confirmed`。
2. 使用 `golden.dsl` 中已经验证的通道标签和结构，置信度 `golden-matched`。
3. 只有波形时，基于结构生成 `candidate`，并列出证据与反例；不得直接改名为已确认角色。

候选结构：

- `sync_window` 应完全或大部分位于同设备 `rxen` 高电平内；
- `sync_pulse` 应是位于 `sync_window` 内的极窄正 pulse；
- `rxen` 通常包含 ramp-up、sync window 和接收尾部；
- `txen` 是独立 active-high 区间，不要求包含 sync 子信号；
- 同设备 TXEN/RXEN 不重入，典型完整顺序是 `TX -> RX` 或 `RX -> TX`；
- 设备 A TX 与设备 B RX 可以同时出现，这是正常跨设备配对，不违反单设备半双工。

若无法可靠划分两个设备的通道组，分别输出候选组合与置信度，不计算要求设备归属的跨设备指标。

## 事务状态机

每台设备独立维护：

```text
IDLE -> TX_ACTIVE -> IDLE/RX_ACTIVE
IDLE -> RX_ACTIVE -> IDLE/TX_ACTIVE
RX_ACTIVE -> SYNC_WINDOW -> SYNC_FOUND -> RX_ACTIVE -> IDLE
RX_ACTIVE -> SYNC_WINDOW -> RX_TIMEOUT_CANDIDATE -> IDLE
```

配对规则：

1. 从 capture 中段寻找完整事件，排除文件起点已为高和文件结尾仍为高的半事件。
2. 对 level 信号按同通道 `rise -> next fall` 配对；出现第二个 rise、缺 fall 或负 duration 时标异常。
3. 只将位于 RXEN 区间内的 sync window/pulse 关联到该 RX 事务。
4. 单设备事件不并发，按时间顺序配对；两个设备分别维护状态，不混用 rise/fall。
5. 没有 sync pulse 时不默认等于 timeout；只有 window/rxen 收尾关系满足已确认容差后才升级为 timeout。
6. 不应用默认毛刺过滤；特别保留单 sample 或双 sample 的 sync pulse 候选。

## 指标字典

| Metric ID | 计算 | 含义/条件 |
| --- | --- | --- |
| `tx_duration` | `txen_fall - txen_rise` | 射频 TX 状态持续时间 |
| `rx_duration` | `rxen_fall - rxen_rise` | 射频 RX 状态持续时间 |
| `rx_rampup` | `sync_window_rise - rxen_rise` | 两个 marker 都存在时计算 |
| `sync_wait` | `sync_pulse_rise - sync_window_rise` | 成功捕获 sync pulse 时计算 |
| `post_sync_rx` | `rxen_fall - sync_pulse_rise` | sync 到 RX 结束；包含剩余 payload/收尾的代理区间 |
| `sync_to_window_end` | `sync_window_fall - sync_pulse_rise` | sync marker 到搜索窗口关闭 |
| `post_window_rx` | `rxen_fall - sync_window_fall` | window 关闭到 RX 结束 |
| `sync_window_duration` | `sync_window_fall - sync_window_rise` | 不把它直接等同于空口包长 |
| `tx_to_rx_turnaround` | `next_rxen_rise - txen_fall` | 同设备先发后收 |
| `rx_to_tx_turnaround` | `next_txen_rise - rxen_fall` | 同设备先收后发 |
| `peer_tx_to_sync` | `receiver_sync_pulse_rise - sender_txen_rise` | 仅同一分析仪/采样时钟且设备映射已确认时；是代理延迟 |
| `rx_timeout_duration` | `rxen_fall - rxen_rise` | 仅已确认 timeout 分类时 |

每个结果保留 sample index 和时间：

```text
time_seconds = sample_index / samplerate_hz
duration_seconds = (end_sample - start_sample) / samplerate_hz
```

结果表至少包含：

| Capture | Device | Transaction | Metric | Start sample | End sample | Duration | Evidence | Status/Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `[生成]` | A/B/unknown | `[生成]` | `[生成]` | `[生成]` | `[生成]` | `[生成]` | observed/proxy/inferred | valid/excluded + reason |

## 采样分辨率

25 MHz 下：

```text
sample_period = 1 / 25,000,000 = 40 ns
```

约 50 ns 的 sync pulse 只相当于约 1.25 个采样周期，因此：

- 可能只出现 1–2 个 high sample，也可能因采样相位而漏检；
- 可以报告捕获到的 pulse 边沿 sample，不可靠测量其真实 50 ns 脉宽；
- 不把单 sample pulse 当毛刺删除；
- 没看到 pulse 不能仅凭 25 MHz capture 证明无线未同步；
- 需要用 `golden.dsl` 统计检出率，或由用户提供更高采样率/展宽后的 marker。

所有时长报告采样率、40 ns 量化步长和显示精度。落在同一 sample 的边沿不能排序。

## 异常与缺失信号

- 只有 TXEN：只计算 TX 区间与间隔，不推断 RX/sync。
- 只有 RXEN：只计算 RX 区间与间隔，不区分成功接收和 timeout。
- 有 RXEN + sync window、无 pulse：输出 `no_pulse_observed`；结合收尾关系可给 `timeout_candidate`，但注明窄 pulse 漏检风险。
- 有 sync pulse、无 sync window：记录 pulse 候选，不能计算 ramp-up/sync wait。
- 同设备 TXEN/RXEN 重叠：标记通道分组、角色识别或采集异常，不强行配对。
- 首尾半事件、缺边沿、重复边沿、同 sample 顺序不明：排除并记录原因。
- 两台设备使用同一逻辑分析仪时可以直接比较 sample；不同分析仪未经同步和漂移校准时不能计算跨设备差值。

## CPU 与包长边界

当前四类信号没有任务、函数、ISR 或调度器 marker，不能计算 CPU execution time，也不能区分抢占、等待、DMA 或睡眠。用户增加软件 GPIO 后，再为每个 CPU 指标定义 start/end 和 elapsed/on-CPU 语义。

TXEN 宽度可以报告为 TX 状态持续时间。若“包长”指字节数，仅凭时长无法确定；还需要 PHY 速率、编码、preamble/header/CRC 和其他空口开销。没有这些信息时只报告时间长度，不换算字节数。
