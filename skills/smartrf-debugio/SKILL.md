---
name: smartrf-debugio
description: "使用 gx-dsview-cli 和 DSView 逻辑分析文件采集并分析 2.4 GHz 无线 DebugIO 时序，覆盖设备能力确认、可配置通道与样本数采集、.dsl/CSV/VCD 波形检查、TXEN/RXEN/SYNC WINDOW/SYNC PULSE 识别，以及发送、接收、同步和条件性的 CPU 耗时计算。用于启动一次逻辑分析仪采样、分析单设备或双设备无线收发顺序、测量 TX/RX 区间、识别接收成功或超时，或诊断无线时序异常。"
---

# SmartRF-DebugIO

## 使用参考资料

- 准备、执行或排查采集时读取 [dsview-cli.md](references/dsview-cli.md)。以当前环境中的 `gx-dsview/docs/cli.md` 为命令接口权威来源。
- 识别信号、配对边沿或计算耗时时读取 [timing-analysis.md](references/timing-analysis.md)。
- 首次建档或关键字段缺失时，只读取 [requirements-qa.md](references/requirements-qa.md) 中与当前任务相关的段落。
- 依赖字段仍未确定时停在对应步骤，不用经验值填补通道、样本数、输出路径或信号语义。

## 遵守核心约束

- 使用 `gx-dsview-cli`，不要混用 DSView GUI、`sigrok-cli` 或其他前端的参数。
- 将“开始扫描”解释为启动一次逻辑采样；将 USB `devices` 操作称为“设备枚举”，避免混淆。
- 当前开发机优先使用 `/home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli`。该路径不存在时，先检查 PATH 中的 `gx-dsview-cli`，仍找不到再向用户索取绝对路径。
- 当前文档目录是 `/home/zhuhy/workspace/projects/ideas/gx-dsview/docs`。其他环境不假定相同用户目录；版本或参数不一致时请求用户提供文档路径。
- 默认选择 device `0`。同一设备只允许一个 CLI/GUI 进程控制；不要递归调用或并发启动第二次 capabilities/capture。
- 把用户给出的多通道常用采样率 `25M` 作为项目基线，而不是设备或所有场景的通用默认值。通道组合变化时依据 capabilities 或用户提供的能力信息校验。
- 默认保留原始 `.dsl`，使用唯一输出路径，不自行加入 `--force`。DebugIO 采集默认加 `--no-private-decode`，除非用户明确要求运行独立的 gx-private 协议解析。
- 区分直接观测的 GPIO 边沿、射频状态的代理语义和真实空口边界。不要把代理事件描述成未经验证的 preamble/SFD 或精确 on-air 边界。
- 现有四类射频硬件信号只能测量无线状态的 elapsed time。没有任务/ISR/软件 GPIO marker 时，明确报告 CPU 时间不可测。
- 本项目已有接线由用户保证；不要把未填写的电气表作为既有采集的阻塞项。若任务包含新接线、改电压或驱动 DUT，再单独确认电气条件。

## 执行工作流

### 1. 确认动作与输入

将请求归入以下一种或多种类型：

- 生成或解释 gx-dsview-cli 命令；
- 启动一次实时采集，再由串口命令刺激 DUT；
- 检查、校验或导出已有 `.dsl`；
- 分析 CSV、VCD 或已确认格式中的无线 DebugIO；
- 比较多个 capture 或诊断 TX/RX/sync 异常。

实际采集前确认物理通道列表、正样本数、唯一输出路径和 CLI timeout。只有在用户要求执行采集时才控制硬件。

### 2. 解析运行环境

1. 将用户提供的 `~` 路径解析为绝对路径，并检查 CLI 存在且可执行。
2. 若当前安装路径不存在，检查 `command -v gx-dsview-cli`；仍不存在时询问用户，不搜索或执行名称相近的程序。
3. 检查动态链接的 `libusb`。当前 Ubuntu 22 环境需使用 `dsview-cli.md` 中记录的系统 libusb 单命令前缀；其他环境先确认实际系统库路径，不照搬。
4. 使用无参数调用或本地 `docs/cli.md` 确认命令面；不要假定存在全局 `--help` 或 `--version`。
5. 第一次使用未知设备配置时先枚举 devices；需要能力信息时串行执行 capabilities。不要把 devices 返回的 `status=unknown` 解释为设备可用。

### 3. 配置并启动采集

1. 从 [dsview-cli.md](references/dsview-cli.md) 展开 current-environment capture 模板。
2. 使用 device `0`、任务给出的物理通道和 samples；没有新依据时采用 `25M` 项目基线。
3. 默认使用无触发固定样本采集。需要边沿触发时，先确认触发通道已启用，并说明成功触发与 pretrigger 尚需结合实际设备验证。
4. 保证输出父目录存在，目标 `.dsl` 不存在；除非用户明确允许，否则不覆盖。
5. 启动 capture 后再执行串口连接/发送命令。CLI 是前台阻塞进程且没有已文档化的机器可读 armed 通知；使用用户确认的启动等待时间，或使用已验证的硬件触发，不凭进程已启动就假定采集已就绪。
6. 同一设备上的串口刺激可以并行进行，但不得再启动第二个 gx-dsview capabilities/capture。
7. 同时检查退出码和 stdout JSON；stderr 只用于诊断。

### 4. 验证并导出采集

1. 成功采集后依次执行 `inspect --json` 与 `validate --json`。
2. 从 capture JSON 检查 `requested_sample_count` 与实际 `sample_count`；从 `inspect` 检查文件实际 samplerate、sample count、物理通道、trigger position 和 `complete`。
3. 保留 `.dsl`，按分析需要导出 CSV 或 VCD；不要把导出文件当作唯一原始证据。
4. 将 exit `13` 且 `capture_status=success` 识别为“采集成功、私有解析失败”的 partial 状态，而不是丢弃 `.dsl`。
5. 发现 busy、unsupported configuration、timeout、disconnect 或 file error 时按结构化错误码处理，不匹配英文 message。

### 5. 识别并分析信号

1. 最多按两个设备、每设备四类信号处理八个通道；为每台设备维护独立状态机。
2. 物理通道映射优先于波形猜测。映射缺失时只输出带置信度的候选角色，不把形状推断写成确定通道名。
3. 支持只有 TXEN/RXEN 或缺少部分 sync 信号的 capture；只计算现有边沿能直接支撑的指标。
4. 识别单设备 `TX -> RX` 或 `RX -> TX` 的半双工顺序；不要因此禁止设备 A TX 与设备 B RX 的正常跨设备重叠。
5. 从 capture 中段选择完整 rise/fall 区间，排除文件首尾半事件、缺失边沿与无法唯一配对的事件。
6. 按 [timing-analysis.md](references/timing-analysis.md) 计算 TX、RX、ramp-up、sync wait、post-sync 和 turnaround 指标。
7. 在 25 MHz 下把采样周期记录为 40 ns；约 50 ns 的 sync pulse 可能只有 1–2 个采样点，禁止默认单点毛刺过滤，也不要声称能可靠测量其脉宽。

### 6. 输出结果

依次报告：

1. 结论、事务方向与异常摘要；
2. CLI 路径/指纹、设备、采样率、通道、samples 和完整命令；
3. 文件 inspect/validate 结果；
4. 通道角色、证据等级和置信度；
5. 单事件时间戳、区间明细和所需统计；
6. 接收成功、候选超时、缺信号和排除样本；
7. 40 ns 量化、窄 pulse 可见性、代理边界和其他不确定性；
8. 原始 `.dsl` 与派生文件位置。

若用户要求 CPU 时间或包字节长度，但没有 CPU marker、PHY 速率或帧结构，分别报告不可测；可以给出无线状态持续时间，不能伪造 CPU execution time 或字节数。
