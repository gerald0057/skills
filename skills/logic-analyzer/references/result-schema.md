# 结果 JSON

成功结果的 `schema_version` 当前为 `1`，主要字段如下：

- `source`：绝对路径、格式、采样率、样本数、通道和归档规模；
- `validation`：本地预检以及 DSL 的 gx inspect/validate 结果；
- `request`：实际通道、窗口和事件明细路径；
- `work_plan`：扫描通道数、估算输入字节和生效的资源限制；
- `measurements[]`：每个通道的边沿、电平覆盖、完整脉宽、周期和极值；
- `warnings`：边界、精度和不可用指标；
- `runtime`：实际分析耗时；
- `event_detail`：仅在显式生成 JSONL 时出现，包含路径、数量、字节数和 SHA-256。

失败输出不创建结果文件，stdout 返回：

```json
{
  "status": "error",
  "error": {
    "stage": "container_preflight",
    "code": "DSL_BLOCK_SIZE_MISMATCH",
    "message": "...",
    "retryable": false,
    "details": {}
  }
}
```

自动化必须以 `code` 分类。`message` 用于人类阅读，可能改写。

`acquire` 成功时不创建分析结果 JSON，只在 stdout 返回紧凑的采集结果：

```json
{
  "status": "success",
  "capture_file": "/absolute/capture.dsl",
  "samplerate_hz": 25000000,
  "requested_samples": 65536,
  "actual_samples": 65536,
  "channels": [4, 7],
  "trigger_position": 0
}
```

字段不可由底层工具确认时可以为 `null`；`capture_file` 是后续 `inspect` 或 `analyze` 的输入。
