# 测量语义

分析窗口使用半开区间 `[start_sample, end_sample)`。边沿位置是新电平开始生效的 sample index。

- rising：前一 sample 为 0，当前位置为 1；
- falling：前一 sample 为 1，当前位置为 0；
- high/low samples：窗口内对应电平覆盖的 sample 数；
- complete pulse：起点和终点边沿都位于窗口内部的电平区间；
- period：同类相邻边沿 sample index 之差；
- high ratio：窗口内 high samples / window samples。

窗口第一个电平区间和最后一个电平区间可能在窗口或采集边界外继续，因此不计入 `complete_pulse_widths`。它们仍计入 high/low sample 总数。

统计采用有界内存在线算法，提供 count、min、max、mean 和总体 stddev。第一版不计算 median 或 percentile，避免为大文件保存全部区间。秒值由 sample 数除以整数 `samplerate_hz` 得到。

CSV 必须是带 `Time(s)`、数字通道、`; Sample rate:` 和 `; Sample count:` 的 DSView transition CSV。CSV 时间映射到声明的采样网格；非法电平、逆序时间或缺失初始状态都会失败。DSView 的 sample-count 注释是面向人类的格式化文本，可能损失精确位数；精确采集终点重要时显式传入 `--end-sample`，周期和完整脉冲等内部边沿测量不受该尾部边界影响。
