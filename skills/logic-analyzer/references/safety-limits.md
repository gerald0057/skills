# 安全与资源限制

工具在读取波形数据前检查普通文件、文件大小、ZIP central directory、entry 数量和名称、加密标志、总解压大小、压缩比、header 长度、DSL 版本、Logic mode、物理通道、block 序列和精确 block 长度。

默认限制以 `scripts/waveform_core/limits.py` 为准。限制值包含输入 4 GiB、16 MiB ZIP central directory、总解压 8 GiB、4096 个 ZIP entry、64 KiB header、120 秒分析 deadline、64 MiB 结果 JSON 和 1 GiB 显式事件明细。工具在交给 Python ZIP reader 前先从文件尾检查 entry count 和 central-directory size，避免超大目录先占用内存。压缩比只是一道早期异常检测；DSL 的声明 sample、probe、block 与实际 entry 尺寸还必须精确一致。

遇到资源错误时按以下顺序处理：

1. 只选择需要的通道；
2. 使用 `--start-sample` 和 `--end-sample` 缩小窗口；
3. 不生成 `--events-output`，只保留在线统计和有限事件样例；
4. 文件来源可信且用户需要完整范围时，才提高单项 `--max-*` 预算。

不要关闭预检，不要绕过 `gx-dsview-cli validate`，不要先把 `.dsl` 导出成完整逐样本 CSV。
