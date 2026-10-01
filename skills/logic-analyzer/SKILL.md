---
name: logic-analyzer
description: "在 Ubuntu 上安全检查、采集并流式测量 DSView 数字逻辑波形，支持经过严格预检的 .dsl 和 DSView transition CSV。用于读取通道、边沿、脉宽、周期、占空比例和基础统计，或通过 gx-dsview-cli 启动 USB 采集；不负责协议解码或信号业务语义判断。"
---

# Logic Analyzer

## 使用工具

使用 `scripts/waveform_tool.py`，不要直接解压、`cat` 或完整导出大型波形。工具 stdout 只返回紧凑 JSON；完整结果写入用户指定的 JSON 文件。

- 初次检查文件时运行 `preflight`。
- 只需元数据和完整性时运行 `inspect`。
- 测量已有 `.dsl` 或 DSView CSV 时运行 `analyze`。
- 只获取 USB 波形文件、不立即分析时运行 `acquire`。
- 自动完成 USB 采集、校验和分析时运行 `capture`。

准备命令或排查 `gx-dsview-cli` 时读取 [references/gx-dsview-cli.md](references/gx-dsview-cli.md)。解释测量字段和边界区间时读取 [references/measurement-semantics.md](references/measurement-semantics.md)。改变资源上限前读取 [references/safety-limits.md](references/safety-limits.md)。消费结果 JSON 时读取 [references/result-schema.md](references/result-schema.md)。

## 遵守安全边界

- `.dsl` 必须依次通过本地文件/ZIP/header/block 预检、`gx-dsview-cli inspect` 和 `validate`，之后才能读取 waveform block。
- 不使用 `--max-*` 放宽限制来自动绕过 `RESOURCE_LIMIT`。先缩小通道或 sample window；只有用户确认文件可信且确有必要时才提高对应预算。
- 默认不输出全部边沿。需要完整事件明细时显式提供 `--events-output`；不要把 JSONL 全文加载进模型上下文。
- 不覆盖 `.dsl`、结果 JSON 或事件 JSONL。为新采集和派生结果使用唯一绝对路径。
- 不从通道名称或波形形状猜测协议、包、ISR、CPU 时间或业务状态。只报告直接可测的数字波形事实。
- 不把首尾边界区间计入“完整脉冲”统计。结果中的 sample index 是权威位置，秒值是按 samplerate 换算的派生值。
- 工具返回错误时依据 `error.stage` 和 `error.code` 处理，不匹配英文 `message`，也不以相同参数无限重试。

## 常用调用

```bash
python3 scripts/waveform_tool.py preflight /absolute/capture.dsl

python3 scripts/waveform_tool.py inspect /absolute/capture.dsl

python3 scripts/waveform_tool.py analyze /absolute/capture.dsl \
  --channel D4 \
  --start-sample 0 \
  --end-sample 25000000 \
  --output /absolute/result.json
```

只有用户需要所有边沿时增加：

```bash
--events-output /absolute/events.jsonl
```

## 选择采集模式

只有用户明确要求控制 USB 逻辑分析仪时才采集。采集期间不需要人与外部系统配合时，使用一体化 `capture`。

采集期间需要用户或外部系统执行测试操作时，使用 `acquire` 把采集边界与后续分析分开：

1. 先列出通道、采样率、样本数、触发方式、超时、输出文件和按 `samples / samplerate` 计算的无触发等待采集时长；用户尚未表示就绪时，等待其确认。
2. 紧邻 `acquire` 调用前明确说明采集即将开始，请在采集期间执行本次测试所需操作。
3. `acquire` 返回后立即说明采集已经结束；失败、超时或取消时也要明确说明采集过程已经终止。
4. 需要测量时，再对生成的 `.dsl` 单独运行 `analyze`。

这些规则描述通用的外部配合，不推断或固化 DUT、协议及用户操作。若用户已明确要求立即开始，无需重复请求确认。同一设备不得与 DSView GUI 或另一个 CLI 并发控制。

## 输出结论

终端结论保持紧凑，引用 `result_file`。读取结果时优先查看 `source`、`validation`、`work_plan`、目标通道的 `edges`、`levels`、`complete_pulse_widths`、`periods` 和 `warnings`，不要为了总结而展开整个事件文件。
