---
name: smartrf-diagnostics
description: 按 SmartRF 协议栈版本分析 `srf_debug`、事件日志、Host/Device 对照快照和本地源码。自动区分 legacy 与 v4.2.x 诊断布局，以及 debug/release/unknown 构建；覆盖连接、跳频、CQA、重传、队列、时序、subinterval、参考时钟、PHY 和精简库日志。用于定位配对连接失败、异常断连、ISR late、丢包、回报率下降和信道质量问题。
---

# SmartRF Diagnostics

## 先识别诊断上下文

在解释字段前，先确定固件版本、日志布局、库变体和源码可用性。优先运行：

```bash
python3 scripts/detect_context.py LOG_FILE [--source-root PATH] [--version 4.2.4]
```

也可将日志通过标准输入传入。脚本路径相对于本 skill 目录。

识别规则：

1. 用户明确指定的协议栈版本优先；否则读取 `[srf_core] stack=X.Y.Z`，再兼容旧格式 `srf_init ok version=X.Y.Z`。
2. `link=conn_impl_c/1.4.0-debug` 中的版本是 link layer 版本，不是协议栈版本。libismbb、librf 版本也不能作为 SmartRF 版本。
3. 多次启动日志出现多个协议栈版本时必须报告冲突，不可静默选择。
4. 没有版本号时，仅可根据 section 布局选择参考资料，不得推断精确 patch 版本。
5. 读取 [version-map.json](references/version-map.json)，再完整读取所选版本的字段和推理资料。

路由：

- v4.2.x 或存在 `[conn_state]`、`[channel_quality]`：读取 [v4.2 fields](references/versions/v4.2/fields.md)、[v4.2 reasoning](references/versions/v4.2/reasoning.md)。
- 旧版单体 `[connected_impl]` 布局或未知旧日志：读取 [legacy fields](references/versions/legacy/fields.md)、[legacy reasoning](references/versions/legacy/reasoning.md)。
- 所有分析都读取 [evidence rules](references/common/evidence-rules.md)；日志裁剪或库变体不明确时再读取 [build variants](references/common/build-variants.md)。

## 决定源码分析模式

按以下顺序发现源码：用户给出的 `--source-root`、当前目录及其父目录中的 `subsys/wireless/smartrf_v4/`、旧路径 `subsys/wireless/smartrf/v4/`。v4.2.x 的具体入口见 [source map](references/versions/v4.2/source-map.md)。

- 源码版本与日志一致：同时分析日志和源码。对关键字段依次确认打印点、数据结构、更新点、清零/生命周期。
- 源码版本与日志不一致：固件日志版本是运行时事实；版本参考资料为主，本地源码只能提供待验证假设。
- 没有源码：仅依据日志和对应版本资料分析，明确无法核对的实现细节。
- release 库：接受诊断字段较少，不把缺字段直接判为异常。

禁止为了找字段而递归读取全部源码。优先 `rg` 精确搜索 section 名、打印标签或字段名。

## 执行分析

1. 分开处理启动/事件时间线与 `srf_debug` 快照，记录 role、state、连接参数和采集时刻。
2. 清点实际出现的 section，并将缺失项标为 `not_captured`、`compiled_out`、`not_applicable`、`unavailable` 或 `unknown_version`；证据不足时不要强行分类。
3. 先检查两端状态、地址、连接参数和 chanmap 一致性，再检查时序/恢复，最后检查 RF→transport→profile/USB 数据路径。
4. 累计计数优先比较清零后固定窗口的增量。单份快照只能证明历史事件；`--stats-clear` 后的区间最大值与链路 lifetime 最大值不可混淆。
5. Host/Device 对照时比较 event、PID、chanmap、当前/目标 hop、错误增量和重传；两端 timer 没有共同绝对时间基准。
6. 输出直接证据、强推断、待验证假设，并按可能性排序。至少用两个相互独立的字段或 section 支撑首要结论。
7. 只建议最小补充采集：同一时间窗口的两端快照、`-s`/`--stats-clear`、故障前后事件日志或必要的 DebugIO 波形。

## 输出格式

按以下顺序组织：一句话结论；版本/构建/源码置信度；状态与时间线；关键跨区证据；正常项和异常项；不确定性；下一步最小验证。保留关键数值和单位，对 16/32 位 event、timer 回绕使用模运算语义。
