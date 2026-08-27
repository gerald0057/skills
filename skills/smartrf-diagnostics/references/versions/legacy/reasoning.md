# SmartRF 诊断推理方法

## 目录

1. 证据规则
2. 推荐检查顺序
3. 跨分区一致性
4. 常见故障特征
5. Host/Device 对照
6. 最小补充采集

## 1. 证据规则

- core 事件日志比事后保留的 handler 快照更直接。`DISCONNECT`、`OUTCOME_PEER_DISCONNECT`、`OUTCOME_SUPERVISION_LOST`、`OUTCOME_LINK_ERROR` 分别指向本地主动、对端主动、超时和内部错误。
- `supervision elapsed == timeout` 且 `queued=yes` 支持超时断连；仅有历史 `sync_err` 或 `timer_late` 不足以证明它触发了断连。
- 累计最大值证明历史上存在尖峰，不证明故障时刻相同。使用 `last time/event` 或前后快照增量建立时间关联。
- `state != CONNECTED` 时先把 `[connected_impl]` 标记为最后连接遗留快照。
- 非原子快照允许相邻计数差 1 或少量事件；数量级差异或持续增长才值得追踪。

## 2. 推荐检查顺序

1. `[link]`：角色、状态、running、bitrate、interval、supervision。
2. core 日志：确定状态转换的直接命令/结果。
3. `[connected_impl]`：恢复阶段、NG streak、late/skip、reference/phase、supervision 和 link error。
4. `[pair_info]` 与 chanmap：两端身份、地址和连接参数是否一致。
5. `[statistics]` 和 `[queues]`：无线可靠性与应用背压。
6. `[ctrl]`：若故障邻近连接参数更新。
7. `[cqa]`：若存在信道选择、跳频或干扰问题。
8. `[phy]`：确认高层配置真实下发和底层状态。

## 3. 跨分区一致性

### Session 与 handler

- `CONNECTED` 应使用 connected handler；`SCANNING` 应使用 scanning；`PAIRED` 通常 handler 为 none。
- `connected=yes` 仅应出现在 CONNECTED/TERMINATING。
- `running=no` 时不应继续产生新的 PHY 统计。

### 地址、速率与连接参数

- 两端 `pair_info access/pipe`、active bitrate、interval、supervision、chanmap 必须相同。
- PHY `access` 和连接期 pipe 配置应匹配 `pair_info`；扫描期 PHY 则应匹配 `adv_address`，不要跨状态比较。
- `conn_chanmap selected` 必须非零且满足实现的最小信道数。

### 数据路径

- Device：producer → `user_enqueue` → `packet send` → 多次 `phy send`（可能重传）。
- Host：`phy rx_ok` → 去重 → `packet recv` → 应用/USB。空包会计入 PHY，不计入 user packet。
- `packet drop`/`q_user overflow` 是明确的发送前丢失；`duplicate` 是空口重传被正确去重，不等同于用户样本丢失。
- 比较速率时清零两端统计并固定采样时长。单次总量跨越不同运行时长没有可比性。

### Timer 与采样

- `timer_late count` 表示晚进入 interval 处理；`skip_intervals` 才表示规划跨过连接间隔。
- Device 主 producer 有效调用数由 `producer_calls main` 体现；late drop 后若设计补调 producer，main 可接近 interval callback，不能只看 `rx_started`。
- subinterval 生效需要同时满足：配置非零、`enabled=1`、运行时周期性 armed、`device_subtimer callbacks` 增长、`producer_calls subinterval` 增长。
- `armed=0` 单次快照可能只是刚处理完 COMP1，不能单独判失效。
- F8K 下 `main` 与 `subinterval` 增量应大致相当；大量 `subtimer late_drop` 会直接减少第二采样点。

## 4. 常见故障特征

### 本地主动断连

直接日志为 `cmd=DISCONNECT state=CONNECTED->TERMINATING`，随后 `OUTCOME_DISCONNECT_DONE`。若同时出现应用 idle/deepsleep 日志，根因在应用 PM 策略，不是 supervision。

### Supervision 断连

出现 `OUTCOME_SUPERVISION_LOST`，且 `elapsed_us` 达到 `timeout_us`、`queued=yes`。继续向前找最先持续增长的 local NG、sync error、late/skip、PHY error或两端频点错位。

### ISR 阻塞或 compare late

故障期间 `timer_late` 增量明显，`max_us` 超过 interval，`skip_intervals` 增长；Device 的 `interval late_drop` 或 `subtimer late_drop` 同步增长。结合 DebugIO 确认没有 RF 动作，而不是窗口打开但未 sync。

### RF 干扰/未同步

`sync_err` 增长而 timer late/skip 稳定，窗口时序和 channel 对齐；CQA RSSI 变差或错误集中在某些信道。CRC error 增长说明已同步但 payload 校验失败，和完全未 sync 要分开。

### 跳频错位/恢复震荡

两端 current/target hop 不一致，`SWEEP`、`switch`、`sweep` 快速增长而 `recovered` 不增长，local NG streak 持续。检查 event skip 是否正确推进共享 hop 序列，以及双方 chanmap/seed 是否一致。

### Device 相位漂移

相位 adjust 长期单向增长、tracking 频繁进入、phase error 单边占优；随后 sync error 和 NG 增长。若误差仍在 RX window 内且没有 sync，优先排除 channel/config，而不是仅归因漂移。

### Host 参考时钟失锁

`HOLDOVER`、ref age 持续增大、stale/rejected 快速增长，correction 停止。若 interval 可整除参考周期却 `DISABLED`，检查平台 ref 注册/有效性。大量 correction 本身不是故障，应看 phase 是否收敛及 blocked 原因。

### 连接参数更新失败

`last_error/reject` 非零，或里程碑停在 req/rsp 某一步；`conn_event param` 长期 STAGED/WAIT_APPLY。核对两端完整参数、instant 是否已过期、supervision 最小约束、控制队列和空口确认。

### 用户队列背压

`q_user max` 接近 capacity、当前水位持续高、overflow/drop 增长。无线重传可能降低 dequeue 速度；应用合并只能保存可合并语义，不能掩盖按键或不可压缩样本的拥塞。

### Transport/cache 生命周期错误

`transport_diag` 或 `transport_payload mismatch` 增长，接收端出现非法 type/len，但 CRC 正常。优先检查 DMA/cache clean、描述符生命周期和生产者复用，不归因空口 bit error。

### CQA 异常

`failed/enqueue_fail` 增长或 `valid=no`；selected map 为零/不足；扫描结果频繁影响连接中冻结的 chanmap。RSSI 全部相同不必然异常，可能是环境底噪或底层测量接口量化，需与已知干扰源对照。

### 低功耗恢复异常

PHY `suspended=yes` 或 `clock=no` 却处于活动连接，PM reject/error 增长，timer 未启动。区分应用主动 deepsleep 断连与底层错误地在连接中 suspend。

## 5. Host/Device 对照

- 对齐同一次连接：比较 pair access/pipe、chanmap、interval 和连接建立日志，不使用墙钟相近作为唯一依据。
- event count 可以比较模 16 位的相对进度；本地 PHY timer 绝对值不能跨设备直接相减。
- Host `phy send` 对应 Device 的 RX 尝试；Device `phy send` 对应 Host 的 RX 尝试，但采样时刻不同会造成尾部差值。
- Device `packet send` 应最终接近 Host `packet recv + duplicate` 的有效用户数据进度；必须考虑清零时刻、在途包和 2x/3x/Nx 应用封装。
- 一端进入 SWEEP 而另一端保持 LOCKED 时，查看双方最近 hop reason、NG 和 skipped event，判断谁先丢失共同进度。

## 6. 最小补充采集

按优先级请求：

1. 故障前后两端完整 core 日志与同一时段 `srf_debug -a`；
2. 两端执行统计 clear 后运行固定 10 秒，再各取 `-a`；
3. 若是瞬时卡顿，启用现有 COMP/RF/USB DebugIO，保留软件 `last time/event`；
4. 若是非法 payload，保存首个异常包及 type/len/data，并读取 transport mismatch；
5. 若是 PHY 配置，提供正常与异常两份 `[phy]` 寄存器快照。

不要把提高 supervision、扩大 RX window 或关闭跳频当作根因修复；这些只能作为隔离实验。
