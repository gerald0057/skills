# 构建变体与分析模式

## 变体识别

- 新格式 `link=<name>/<version>-debug|release` 直接给出 link layer 变体。
- manifest 可辅助确认已链接库，但不能替代运行日志。
- 没有变体信息时标记 `unknown`，不要因为诊断丰富就自动称为 debug。

## 分析矩阵

| 本地源码 | 版本匹配 | 库变体 | 分析方式 |
|---|---|---|---|
| 有 | 是 | debug | 全字段与更新路径核对 |
| 有 | 是 | release | 以公开状态和有限计数为主，接受内部诊断裁剪 |
| 无 | - | debug | 按版本完整 schema 做日志分析 |
| 无 | - | release | 保守分析，不从缺字段推断根因 |
| 有 | 否 | 任意 | 日志/版本资料为主，源码仅作假设 |

## release 日志注意事项

release 通常关闭内部 trace、hook、详细诊断结构和 debug dump。仍可依赖初始化版本、公共事件、link 状态、公开统计和错误码。无法观察内部 recovery、phase 或 per-channel 计数时，应请求 debug 固件复现，而不是断言对应机制未运行。

