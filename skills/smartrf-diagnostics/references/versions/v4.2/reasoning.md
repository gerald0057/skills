# SmartRF v4.2.x 推理指南

## 先建立数据路径

按 `producer/profile → q_user → stop-and-wait/PHY → Host packet/profile → USB` 分层。一个层的成功计数不能替代下一层：Device `packet send` 正常仍可能在 Host profile 解包或 USB submit 丢失。

## 连接后单向 sync

优先对照两端 `pair_info`、`connected_address`、PHY `access/pipe/rx_mask`。若 Device 能 sync Host、Host 不能 sync Device，再看 Host RX window/rxtov、Device TX 时点、bitrate/ramp profile 和 chanmap，而不是先归因于配对数据库。

## 重传、重复包与丢包

- Device `retransmit total` 增长且 Host `duplicate` 同步增长：Host 已收到数据但 ACK/下一状态未被 Device确认，检查反向链路和 Host 消费阻塞。
- Host duplicate 很高、USB 未连接：可能是上层不消费造成 stop-and-wait 重复，不直接代表 RF CRC/sync 差。
- `packet drop` 增长而 queue overflow=0：检查 profile 压缩边界、transport 返回值、接收解包和 USB submit。
- 平均重传低但 max 高：存在短促干扰或 ISR 卡顿；用区间 max 与 lifetime max 区分当前问题和历史峰值。

## CQA 三阶段约束

1. Device 应用参数生成 `param_allow`。
2. Device CQA 在该范围内产生较宽的 `device_allow`。
3. Host 从请求的 `device_req` 内结合 Host CQA 产生 `final`。

任一步的结果都不得超出输入 allow。若 `final` 含输入之外信道，是协议/位图传递错误；若 selected 数不足但 fallback=yes，检查 fallback 仍在输入范围。连接后的 `[conn_radio] map` 应与 Host final 及 Device收到的结果一致。

## 当前信道质量

`[channel_quality]` 是按驻留次数累计的观测，先计算每个信道的错误增量/`rx_done`，不要只比较绝对错误数。驻留策略允许干净信道长期不跳，其他选中信道为零不异常。当前版本标记 `policy_input=no` 时，统计不会自动触发跳频。

## ISR late 与 subinterval

- 主 interval：比较 `timer_late`、`device_interval late_drop`、`rx_started` 与 callbacks。
- 子 interval：比较 `device_subtimer callbacks`、`late_drop`、`producer_calls subinterval`。callbacks 很高但 producer calls 少，说明 late/unarmed 策略丢弃采样。
- `drop_raw` 是未补偿视角，`drop_adjusted` 已扣固定 timer-to-subinterval latency；判断算法有效性以 adjusted 为主，同时保留 raw 观察硬件到 ISR 延迟。
- F8K 250 us interval + 125 us subinterval 应接近每个 interval 一次 main、一次 subinterval producer。只有 COMP1 中断波形而 producer 不增，仍不能认为 profile采样有效。

## Host 参考时钟同步

LOCKED 且 accepted 持续增加、phase filtered 靠近目标、correction缓慢增长通常正常。HOLDOVER/stale 增长说明参考采样停止；rejected 大量增长而 valid=yes，检查整除策略、样本 age/jitter 与 ACK/rate 阻塞。不要把尚未采样时的 phase range 哨兵值当溢出。

## Device 跟随风险

Host 每次微调会通过空中 sync 反映给 Device。检查 Device `phase_correction` 是否平滑、`device_tracking` 是否频繁进出、`rx_ng`/recovery 是否同步上升。Device adjust 单向长期累积且数值远大于 interval，优先核对“当前计划 anchor 与累计显示量”的语义，不仅凭总量断言跟不上。

## 断连判定

- `supervision elapsed` 接近 timeout 且 rx_ok 停止：无线失联。
- `enqueue_fail` 非零：监督事件投递失败，状态机可能延迟退出。
- `link_error` 非零：以首次错误日志、site/error 为主；leave 后计数可能是残留。
- 主动 `DISCONNECT`、idle/deepsleep 与 supervision lost 必须从事件时间线区分。

## release / 缺失源码

release 库缺少 conn 内部 section 时，仅使用公开 link、事件、统计、CQA/PHY（若开启）下结论。建议复现 debug 库或采集两端窗口统计；不要假设缺失的 recovery、phase、channel_quality 都为零。没有源码时避免解释枚举数字的未公开精确含义，保留原值并描述上下文。

