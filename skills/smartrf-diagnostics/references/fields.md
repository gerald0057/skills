# `srf_debug -a` 字段参考

## 目录

1. 通用解释规则
2. `[link]`
3. `[connected_impl]`
4. `[pair_info]` 与 `[pending_pair_info]`
5. `[handlers]`
6. `[queues]`
7. `[statistics]`
8. `[ctrl]`
9. `[cqa]`
10. `[phy]`

## 1. 通用解释规则

- `srf_debug -a` 是一个非原子快照。打印期间 ISR 仍可能更新字段，邻近计数相差少量不一定是错误。
- `count`、`max`、`overflow`、`switch` 等通常是自初始化或最近一次 clear 后的累计值；当前状态由 `state`、`stage`、`streak`、`elapsed`、`pending` 等字段表达。
- 已断开时，`[connected_impl]` 常保留最后一次连接快照，用于事后诊断；不要将它当作仍在运行。
- timer 和 event 会回绕。event 通常为 16 位，PHY timer 通常为 32 位；差值必须使用相应的 wrap-safe 语义。
- `yes/no`、`valid` 和 `pending` 代表采样瞬间状态，不代表此前从未变化。

## 2. `[link]`

- `role`：本端逻辑角色，`host` 先 TX，`device` 先 RX。
- `state`：当前 session 状态；常见 `UNPAIRED`、`PAIRED`、`SCANNING`、`PAIRING`、`CONNECTING`、`CONNECTED`、`TERMINATING`。
- `prior_state`：进入当前状态前的状态。断开后它可能用于说明来源，但不是完整历史。
- `handler`：当前 session handler；状态与 handler 应匹配。
- `initialized/running`：协议栈资源是否初始化、是否已 start。
- `pair_window`（Host）：`mode`、窗口毫秒数、是否开放、已用/剩余时间及超时后忽略的配对请求数。它不限制已有配对信息的连接请求。
- `paired/connected`：pair info 是否有效，以及是否处于 CONNECTED/TERMINATING。
- `local_mac`：本端运行时 MAC。与有效 pair info 中本角色 MAC 长期不一致需要核查存储迁移或旧配对信息。
- `network`：组网字符串的 hash 及是否使用默认 key。两端必须处于同一发现域才能使用相同广播地址体系。
- `adv_address`：广播 access、配对 pipe 及连接请求 pipe；这是发现阶段地址，不等于已连接的 pair access/pipe。
- `bitrate`：请求值、实际 PHY 值及是否因硬件能力降级。两端 active bitrate 必须一致。
- `conn_interval`：当前已应用的连接间隔，单位 us。
- `supervision`：协议字段值及单位；实际超时为二者乘积。
- `phy_time curr/cmp`：当前本地 timer 和下一 compare；只在同一设备本地比较。
- `phy_timing`：profile 的 RX guard、TX/RX 相对时序、timer 到 TXEN/RXEN 的固定延迟、RX advance/lead。它描述正常规划预算，不等同于随机 ISR late。

## 3. `[connected_impl]`

### 3.1 公共连接状态

- `impl/design/first_action`：当前连接实现、设计模型和首个无线动作。
- `instant_snapshot`：建连 instant 是否有效及绝对目标。
- `handover interval/first_comp`：handler 接管后的连接间隔和下一 compare 基线。
- `conn_event`：当前 event count、连接参数 apply instant、参数状态、跳频 hold 状态及待应用连接间隔。`STAGED/WAIT_APPLY` 持续不收敛才异常。
- `stop_wait`：当前 TX 描述符类型、2-bit TX PID、期望 RX PID、producer 是否注册。`EMPTY` 是协议空包，不等同于无线没有动作。
- `producer_timing`：主/子采样配置。`subinterval` 为已应用 offset，`pending` 为待应用值，`latency` 是 compare 到 callback 的固定预算，`comp0/target` 是本轮基准与 COMP1 目标，`armed/enabled` 是瞬时状态。
- `host_rxtov`：Host 当前 RX timeout、软件验证值、硬件值和 pending 值。Device 只保存 Host 所有的协商值；两端 active 应一致。

### 3.2 Host 参考时钟同步

仅 Host 且功能启用时出现：

- `host_ref_sync state`：`DISABLED/ACQUIRE/SLEW_IN/LOCKED/HOLDOVER`；`target` 是相对于参考 tick 的目标相位。
- `pending/streak/stable/acquire`：待执行步长、误差方向连续性、稳定计数和捕获计数。
- `ref_policy`：每多少 connection event 处理一次、期望参考 tick 数、最大容忍跨度和当前 anchor。
- `ref_warning`：连接间隔无法整除参考周期，因此校正被禁用。
- `ref_clk`：平台读取结果、有效性、参考周期、sequence、最近时间戳和样本 age。
- `ref_phase`：原始/滤波误差、当前 jitter 和历史原始范围。
- `ref_samples`：接受、拒绝、陈旧、sequence gap 数和 jitter 范围。
- `ref_correction`：累计校正、提前/延后次数、最后一步及 event/time。
- `ref_blocked`：因未收到 ACK 或速率限制而禁止校正的次数。

### 3.3 信道与恢复

- `radio`：当前 channel、hop index、已锁定 index、目标 index、当前/下一 chanmap 通道数及更新 pending。
- `conn_chanmap`：来源、40-bit map 和选中通道数。`selected=0` 或两端 map 不同是严重异常。
- `recovery`：`LOCKED/RENDEZVOUS/SWEEP`、本端/对端连续 NG、dwell、对端 hop 是否 armed、本地切换 pending 和 sweep offset。
- `hop_history`：累计切换、会合、扫频和恢复次数。
- `last_hop`：最后切换 event、index/channel 前后值、原因、当时 NG 及是否还有 apply pending。

### 3.4 PHY、interval 与 subinterval 诊断

- `phy_errors`：PHY API 错误累计数和最近错误码。
- `timer_late`：连接 interval callback 被判 late 的次数、最大 late 及累计跨过的 interval 数。
- `timer_late_last`：最近 late 大小和本地时间。
- `producer_calls`：主 interval 与 subinterval producer 实际调用次数。
- `device_interval`：Device COMP0 callback、成功 RX start、PHY busy 和 late drop 次数。通常 `callbacks = rx_started + busy + late_drop` 附近成立。
- `device_subtimer`：COMP1 callback、未 armed、early、late drop；`drop_raw last/max` 是未扣除固定延迟的值，`drop_adjusted` 是用于判定的校正值。
- `device_timer`：最近 interval event、callback time、anchor、late 和本次 skipped 数。
- `device_sync`：成功同步累计数及最近 event、SYNC、推算 TXEN、anchor、相位误差。
- `device_reanchor`：最近是否重锚，以及重锚前后 next target。
- `device_phase_err`：正负误差样本数、极值和重锚次数。
- `device_rx_ng`：无效接收累计数、当前 streak、首个和最近故障 event/time。
- `device_phase`：有界相位累计 adjust、实际使用值、下次 RX target、待执行步和方向 streak。
- `device_tracking`：连续漂移跟踪是否激活、方向、稳定样本及进入/退出次数。
- `phase_correction/phase_last`：相位步进总数、提前/延后构成、累计范围和最近一步。

### 3.5 连接存活与错误

- `supervision elapsed_us/timeout_us`：自最近有效交互后的累计失联时间和阈值；`queued` 表示 lost outcome 已投递。
- `enqueue_fail/last`：supervision outcome 入 core 失败次数和最近错误。
- `link_error raw/site/error`：编码后的最近致命错误、发生站点及错误码。非零时优先追踪具体 site。
- `diagnostics compiled_out`：构建关闭了详细诊断，缺失字段不能解释为计数为零。

## 4. `[pair_info]` 与 `[pending_pair_info]`

- `valid/magic`：存储结构是否有效。
- `mac_host/mac_device`：配对双方身份。
- `access/pipe`：连接后的 access address 和通信 pipe address。
- `connected_address`：以实际 PHY pipe index 展开的同一地址。
- `pending_pair_info`：配对或更新尚未提交的候选信息；正常稳态通常无效。失败后长期有效需检查事务清理。

## 5. `[handlers]`

- `pairing`：Device 配对状态、剩余重试、广播信道 index、timer 是否启动、TX 长度。
- `connecting_host/device`：角色专属连接阶段、退出时是否保留 timer、instant 是否有效、TX 长度。
- `instant_us/instant_target/exit_target`：协商提前量、连接 instant 和 handler 退出目标。
- `event_target/event_wait_us/event_txen/window_deadline`：Device 请求/响应窗口和最后 TXEN 锚点。
- `adv_index`：当前发现信道索引。
- `scanning`：Host scan 状态、待发响应长度、广播信道索引和 timer 所有权。
- `req_reject`：pair/conn/unknown 各 7 类拒绝累计，顺序为 `ADDR/CRC/PARSE/PAIR_INFO/CONN_INFO/REPLY_TX/COMMAND`。
- `req_reject_last`：最近拒绝的原因、flags、包 type、pipe、len、PID、CRC 和本地时间。

## 6. `[queues]`

- `q_user`：用户队列当前使用量、容量、历史最大水位、空/满及 overflow。overflow 与统计中的 `packet drop` 同源。
- `q_proto_tx`：控制队列的相同水位信息。长期满可能阻塞连接参数或断开控制过程。
- `transport_diag`：transport 内部契约错误累计及 bit flags；非零通常不是普通无线丢包。
- `transport_payload`：首字节/type 不匹配累计及最近期望值、实际值、长度和 descriptor 类型，可指向 payload 生命周期或 cache 一致性问题。
- `q_core`：core action/event 队列水位。满时可能丢失状态转换 outcome。

## 7. `[statistics]`

- `transport user_enqueue/proto_enqueue`：成功进入 transport 的用户/控制描述符数。
- `phy send`：实际发起 PHY TX 次数，包含空包、控制包和重传。
- `phy rx_done/rx_ok/crc_err/sync_err`：RX 完成、有效包、CRC 错和未同步/超时。
- `packet send/recv/drop/duplicate`：成功提交的用户包、交付给上层的用户包、用户队列丢包和链路去重包。
- `hop switch/rendezvous/sweep/recovered`：跳频恢复累计行为。

计数口径不同，不能直接要求 `phy send == packet send`。分析速率时必须用两次快照的增量除以实际时间。

## 8. `[ctrl]`

- `conn_param last_error/reject`：最近连接参数过程错误及 reject reason。
- `supplied/required`：被拒 supervision 值和按约束要求的最小值。
- `local_req/peer_req/rsp_queued/rsp_confirmed/peer_rsp`：各里程碑是否有效及对应 event/time。
- `req_to_rsp`：本地请求到对端响应的 wrap-safe event 数和时间。出现负时间通常先检查旧里程碑未清、timer 回绕或非同一次 procedure，不直接认定空口负延迟。

## 9. `[cqa]`

- `available`：仅 Host；可能因禁用、尚未分配或角色不符不可用。禁用时 `fallback` 给出固定 map。
- `policy`：bitrate、全量/增量每信道样本数、每次增量信道数、优选/最小信道数和最小频间距。
- `scan generation/full/incremental/failed/enqueue_fail`：结果代次、扫描类型累计和失败情况。
- `last type/count/cursor/error/valid`：最近扫描类型、信道数、下次 cursor、错误和结果有效性。
- `channels measured/selected/maps`：已测/已选数量和位图。
- `rssi chN`：各已测物理信道的 RSSI；数值越负通常越安静。选择结果还受允许位图和 spacing 约束，不能只按最低 RSSI 排序复算。

## 10. `[phy]`

- `software`：PHY 初始化、当前 IDLE/TX/RX、底层角色和 clock 状态。
- `lowpower`：低功耗使能、当前 suspend、timer 是否启动及 manual AGC。
- `pm`：suspend/reject/restore/resume 累计和最近错误。
- `config`：bitrate、channel、TX pipe、RX mask、access、CRC init、RX timeout、TX power。应与 link、pair 和角色一致。
- `time`：当前/compare、最近 TXEN/RXEN/TXDONE/RXDONE/SYNC；profile 的 TX/RX timer latency、subinterval latency、RX advance/lead 和 plan guard。
- `rf version/pll`：底层 RF 固件/库版本和 PLL 控制快照。
- `registers`：原始硬件寄存器。优先用于比较正常/异常快照及验证高层 config 是否真正下发。没有对应芯片寄存器定义时只报告差异，不猜 bit 含义。
- `INTEN/INTSTAT/FSMSTAT`：可辅助判断中断使能、残留 flag 和硬件 FSM；单次非零 snapshot 可能只是采样竞争。
- `PKTCFG/PKTCFG2/BASE/PREFIX*/TXADDRSEL/ADDRMATCHEN`：包格式和地址配置，应与 bitrate、access、pipe/mask 相符。
- `TXRXTIMING/TXRXCFG`：收发 timing 和 RX timeout 的硬件编码。
- `CRCCFG/CRCINIT`：CRC 配置。
- `RFTXCFG`：射频发射配置，包括功率相关设置。
- `RXSTAT/RXSTAT2`：最近 RX 状态；必须结合 `rx_ok/crc_err/sync_err` 和 driver 定义解释。
