# SmartRF v4.2.x 诊断字段

本说明按 v4.2.4 校准，适用于 v4.2.x 的分区式诊断。patch 版本或 release 库可能裁剪字段；缺失字段按通用证据规则分类。

## 初始化版本行

新格式通常为：

```text
[srf_core] stack=4.2.4 link=conn_impl_c/1.4.0-debug
```

- `stack`：SmartRF 协议栈版本，是诊断路由的主版本。
- `link`：连接后 link layer 实现、实现版本和 `debug|release` 变体。
- PHY 初始化可能另外打印 libismbb/librf 日期版本；它们不是 stack 版本。

## `[link]`

- `role/state/prior_state/handler`：当前角色、会话状态、前态和 handler。状态与 handler 不匹配优先检查转换未完成或断开后残留快照。
- `initialized/running/paired/connected`：生命周期和配对/连接标志。
- `pair_window`：Host 是否仍允许新配对；`open=no` 不影响已有配对设备连接。
- `local_mac/network/adv_address`：本机、网络 key hash、广播 access/pipe。两端 network 和已配对地址必须匹配。
- `bitrate`：requested、active、hardware_limited。分析空中时序必须使用 active。
- `conn_interval/supervision`：连接间隔和监督超时。
- `phy_time/phy_timing`：当前 timer compare 与规划用的 ramp/guard/advance/lead 参数；不是两端共同绝对时间。

## `[connected_impl]`

仅声明实现名称与设计摘要。具体状态已拆到后续 `conn_*` section。release 库可能只保留此摘要和有限公共状态。

## `[conn_state]`

- `first_action`：Host/Device 在 instant 的首动作。
- `instant_snapshot`：连接切换目标是否有效。
- `handover interval/first_comp`：当前连接间隔与首个 compare。
- `conn_event`：事件计数、参数切换 instant、pending interval。连接参数更新期间核对 `pending_interval` 和 applied 状态。
- `rf_ramp`：当前/待应用 ramp 模式和 pending 标志；FAST/AUTO 切换应在 RF idle 安全点生效。

## `[conn_sync]`（通常 Host）

- `host_ref_sync`：参考时钟同步状态与收敛 streak。DISABLED/HOLDOVER 需结合 ref 时钟是否有效。
- `ref_policy`：stride、修正窗口和 anchor；连接间隔能被参考周期整除才可稳定校正。
- `ref_phase`：raw/filtered/jitter/range。极值初始哨兵值且 accepted=0 时不代表真实超大相位。
- `ref_samples`：accepted/rejected/stale/gap。持续 rejected 且 accepted=0 表明参考样本不可用或策略未接纳。
- `ref_correction`：advance/delay 和最近修正。
- `ref_blocked`：因 no_ack 或 rate 限制而未修正的次数。

## `[conn_transport]`

- `stop_wait tx/tx_pid/rx_expected_pid`：stop-and-wait 当前发送类型与 PID。PID 是否推进必须结合 ACK 和 duplicate 判断。
- `producer_timing`：subinterval offset、pending、固定 latency、COMP0/目标和 armed/enabled。F8K 等模式需要 enabled/armed 与 producer call 同时成立。
- `host_rxtov`：当前/待更新/硬件值以及 owner；两端参数应一致理解。

## `[conn_radio]`

- `radio channel/hop/locked/target`：当前频点、hop index、已锁定/目标 index。
- `chmap/next/pending`：当前和下一信道数量及待应用状态。
- `conn_chanmap map`：当前连接信道位图。Host/Device 必须一致；位图选中数量应与 `chmap` 相符。

## `[channel_quality]`

这是当前连接所选信道的运行统计，`scope=current_link counters_only policy_input=no` 表示只诊断、不参与跳频决策。

- `idx/ch/cur`：chanmap 索引、物理信道、当前驻留标记。
- `rx_done/rx_ok`：接收完成和有效接收。
- `sync/crc/addr`：同步、CRC、地址过滤错误。
- `duplicate`：相同 PID 的重复有效包。Host 上层不消费或 ACK 路径异常时可能上升。
- `retransmit`：该信道上的逻辑重传。

允许驻留在干净信道，因此未访问的已选信道计数为零是正常现象。比较质量时使用增量，避免用累计值偏向驻留时间最长的信道。

## `[conn_recovery]`

- `recovery stage/local_ng/peer_ng/dwell/armed/pending`：两阶段跳频恢复状态和连续失败计数。
- `sweep_offset`：扫描偏移。
- `hop_history switch/rendezvous/sweep/recovered`：各类跳频与恢复累计数。
- `last_hop`：最近跳频事件、index/channel 前后值、reason、NG 和 pending 状态。

频繁 `sweep`、低 `recovered` 且 sync error 增长说明信道/同步恢复压力；长期无 hop 且当前信道无错误可能只是正常驻留。

## `[conn_timing]`

- `phy_errors`：PHY API/ISR 错误累计及最近值。
- `timer_late count/max_us/skip_intervals`：主 interval compare 迟到及跳过间隔。`count` 与 `skip_intervals` 含义不同，小幅 late 不必然跳帧。
- `producer_calls main/subinterval`：主采样和子间隔 producer 调用次数。
- `device_interval callbacks/rx_started/busy/late_drop`：Device 主 interval 调度结果。
- `device_subtimer callbacks/unarmed/early/late_drop/drop_raw/drop_adjusted`：COMP1 子间隔时序；判断 F8K 是否生效的核心字段。
- `device_timer/device_sync/device_reanchor`：计划 anchor、实际同步和重锚信息。
- `device_phase_err/device_phase/device_tracking/phase_correction`：Device 跟随 Host 的相位测量、累积修正和状态。
- `device_rx_ng`：Device RX NG 次数、streak 和首末事件。

`late_drop` 后若下一 interval producer 仍被调用，应用可能通过合并补采样；是否补到接收端还要看 profile/transport/USB 统计。

## `[conn_errors]`

- `supervision elapsed_us/timeout_us/queued/enqueue_fail/last`：监督超时进度及事件投递情况。
- `link_error raw/site/error`：连接实现错误编码。非零时结合事件日志中的首次错误分析，退出后的快照可能已清部分状态。

## `[pair_info]` / `[pending_pair_info]`

- `valid/magic/mac_host/mac_device/access/pipe`：已生效或待提交的配对信息。
- `connected_address`：实际配置给连接 PHY 的 access/pipe。它必须与有效 pair info 对应；不一致会造成一端能 sync、另一端不能。

## `[handlers]`

- `pairing/connecting_host/connecting_device/scanning`：各 handler 内部状态、retry、instant/deadline、event target。
- `req_reject` 与 `req_reject_last`：请求拒绝分类和最近报文元数据。累计拒绝需用增量判断。

## `[queues]`

- `q_user/q_proto_tx/q_core`：当前、容量、峰值、空满和 overflow。
- `transport_diag`：传输层错误标志。
- `transport_payload mismatch`：长度/类型契约异常。queue overflow=0 不能证明 USB/profile 没有丢弃。

## `[statistics]`

- `transport user_enqueue/proto_enqueue`：用户与协议包进入 transport 的数量。
- `phy send/rx_done/rx_ok/crc_err/sync_err`：物理动作与接收结果；`send` 包含重传和空包，不等于用户 sample。
- `packet send/recv/drop/duplicate`：逻辑包层计数。`drop` 可来自发送队列失败或协议策略，按源码/版本确认。
- `retransmit total/max/avg/current`：区间总重传、区间最大、低精度平均和当前包尝试数；若另有 lifetime max，则它不被 `--stats-clear` 清零。
- `hop switch/rendezvous/sweep/recovered`：连接期跳频统计。

`srf_debug -s` 输出当前统计；`srf_debug --stats-clear` 应先输出再清区间统计。重连/重新初始化是否清零需从事件和源码核对。

## `[ctrl]`

连接参数请求/响应的 valid、event、time 以及 `req_to_rsp`。切换失败时按 local request→peer response→schedule→apply 顺序找断点。

## `[cqa]`

v4.2 支持 Device 和 Host 独立 CQA：

- `role/policy`：角色、码率、full/incremental sample 数、每轮增量信道数、preferred/minimum/spacing。
- `scan generation/full/incremental/failed/enqueue_fail`：扫描轮次和失败。
- `last`：最近扫描类型、数量、cursor、error、valid。
- `selection_input`：Device 为参数初筛 `param_allow`；Host 为 Device 请求中的 `device_req`。
- `selection_result`：Device 二筛的 `device_allow`；Host 终筛的 `final`。
- `fallback`：若筛选不足是否使用保底。保底不得越过用户 allow 范围；用户只允许一个信道时可继续单信道连接。
- `channels measured/selected/measured_map/selected_map` 与逐信道 RSSI：本角色实际测量和选择结果。

Host 的 `measured=20` 可能是其后台全量/增量 CQA 能力，不代表连接请求忽略了 Device allow；判断建连约束必须看 `selection_input device_req` 和 `selection_result final`。

## `[phy]`

- `software/state/role/clock`：PHY 软件状态、角色与 RF clock。
- `lowpower/pm`：低功耗、suspend/reject/restore/resume 和最近错误。
- `rf_clock policy/current`、`clk_gate`：动态 RF clock gate 策略与启停失败统计。Host 通常保持常开。
- `config`：bitrate、channel、pipe/mask、access、CRC、rxtov、发射功率。
- `rf_profile`：AUTO/FAST、TX/RX rampup；必须与 PLL/clock 策略和连接间隔匹配。
- `time`：curr/cmp、TXEN/RXEN/TXDONE/RXDONE/SYNC、timer latency、subinterval、advance/lead/guard。
- `rf version/pll`：底层库/硬件状态。
- `registers`：原始寄存器，仅在软件状态与硬件行为矛盾时深入解码；release 或关闭 PHY debug 时可能不存在。

