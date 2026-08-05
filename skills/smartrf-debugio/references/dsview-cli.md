# gx-dsview-cli 采集参考

本文档按 2026-07-16 的本地 `gx-dsview` 文档和用户需求配置。命令接口以目标环境的 `docs/cli.md` 为准；路径和动态库配置属于环境信息，不是跨机器常量。

## 目录

- [路径解析](#路径解析)
- [当前环境快照](#当前环境快照)
- [调用约定](#调用约定)
- [设备与能力](#设备与能力)
- [启动一次采集](#启动一次采集)
- [采集后检查与导出](#采集后检查与导出)
- [退出码](#退出码)
- [能力边界](#能力边界)

## 路径解析

按以下顺序确定 CLI：

1. 使用用户为当前环境提供的绝对路径；
2. 若仅提供 `~` 路径，先展开为当前用户的绝对路径；
3. 未提供或路径不存在时，检查 `command -v gx-dsview-cli`；
4. 仍未找到时向用户询问，不猜测安装前缀，也不改用 DSView GUI 或 `sigrok-cli`。

当前环境：

```text
CLI  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli
DOCS /home/zhuhy/workspace/projects/ideas/gx-dsview/docs
```

在脚本或参数数组中使用绝对路径；不要依赖子进程替你展开 `~`。其他环境可能同时改变二进制和 docs 位置，应由用户告知或从 PATH 发现 CLI，再核对对应版本文档。

## 当前环境快照

| 字段 | 当前值 | 证据/边界 |
| --- | --- | --- |
| OS/架构 | Ubuntu 22，x86-64 | 用户需求；本地 ELF 检查 |
| CLI | `/home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli` | 文件存在且可执行 |
| CLI SHA-256 | `1d0bbbe8cf76637872578c4a9192f9e67bf269b0a5424f215557c9dcaaf9af65` | 每次二进制更新后重记 |
| 文档仓库 commit | `8e4de8e2aa7543f9704fb6b91ca193ee9e0145b2`，工作树干净 | 本轮只读检查 |
| 权威 CLI 文档 | `/home/zhuhy/workspace/projects/ideas/gx-dsview/docs/cli.md` | 参数和退出码来源 |
| 设备选择 | `--device 0` | 用户要求；不同环境仍需确认枚举顺序 |
| 多通道采样基线 | `25M` | 用户要求；不是设备通用最大值 |
| 原始文件 | DSView v2/v3 logic `.dsl` | v1/Analog/DSO 不在当前无头分析范围 |
| 导出格式 | `csv`、`vcd`、`gnuplot` | gx CLI 当前格式 ID |

当前安装版 SHA 与仓库测试报告中的 final-clean SHA 不同，因此不要把 final-clean 的硬件通过记录当作该安装二进制的逐字节验收。本轮只运行了不访问硬件的 usage/文件/链接检查，没有执行 devices、capabilities 或 capture。

### 当前 Ubuntu 22 动态库前缀

当前 shell 下，CLI 默认会解析到 T-Head DebugServer 自带的 `libusb-1.0.so.0`；gx 测试报告记录该组合曾导致 `DRIVER_INIT_FAILED`。本机执行 gx 硬件命令时使用：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli <command> <options>
```

该系统库路径只适用于当前 Ubuntu 环境。其他机器先用 `ldd <absolute-cli-path>` 检查 `libusb`，再由用户确认正确系统库目录；不要盲目复制此 `LD_LIBRARY_PATH`。

## 调用约定

```text
gx-dsview-cli <devices|capabilities|capture|decode|export|inspect|validate> [options]
```

- 当前二进制没有已文档化的全局 `--help` 或 `--version`；无参数调用只打印 usage 并返回 2，`--help` 被当作未知 command。
- 使用 `--json` 时，stdout 只含一个 JSON 文档；驱动日志和人类错误写 stderr。
- 同时检查进程退出码和 JSON `status`/`error.code`；不要匹配英文 message。
- `--json`、`--force`、`--no-private-decode` 都是不带值的布尔选项。
- 输出父目录必须存在。默认拒绝覆盖；只有用户明确授权替换目标时才使用 `--force`。
- CLI 通过同目录临时文件完成后原子发布。失败、timeout 或 cancel 时不要把残留或缺失文件冒充成功。
- 一个进程只运行一个采集会话；同一设备上的 devices/capabilities/capture 按顺序调用，不递归，不与 DSView GUI 或第二个 CLI 并发 claim。

## 设备与能力

“设备枚举”不是本 Skill 所说的“开始扫描”。枚举只列出 USB 设备：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli devices --json
```

`devices` 不 open/claim；返回的 `status=unknown` 不表示设备空闲。当前项目始终选择列表中的 device 0，但如果环境包含多台设备，先让用户确认列表顺序。

`capabilities` 会 open/claim 设备，因此不能与其他控制进程并行：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli \
  capabilities --device 0 --json
```

读取 `channel_modes`、`samplerates_hz`、`max_sample_depth` 和 `triggers`。采集会精确校验通道组合与采样率，不会静默降级。以下情形重新查询 capabilities：

- 换设备、换 CLI/固件或首次运行；
- 通道数量、最高物理通道或采样率变化；
- 返回 `UNSUPPORTED_CONFIGURATION`；
- 用户要求边沿触发或更高采样率。

## 启动一次采集

### 必需参数

| 参数 | 规则 |
| --- | --- |
| `--samplerate RATE` | 整数 Hz 或 `k/M/G[Hz]`；本项目多通道常用 `25M` |
| `--channels LIST` | 物理数字通道索引，逗号分隔 |
| `--samples COUNT` | 正整数；根据目标观察窗口计算 |
| `--output PATH` | 唯一 `.dsl` 路径，父目录已存在 |

请求观察窗口为 `duration_seconds` 时：

```text
requested_samples = ceil(samplerate_hz * duration_seconds)
```

驱动按 1024 samples 对齐。JSON 中同时保留 `requested_sample_count` 和实际 `sample_count`；后续时间轴使用实际采样率和 sample index，不假设两者相等。

### SmartRF 无触发模板

这是模板；执行前替换 `CHANNELS`、`SAMPLES`、`TIMEOUT` 和输出文件名：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli capture \
  --device 0 \
  --samplerate 25M \
  --channels CHANNELS \
  --samples SAMPLES \
  --trigger none \
  --timeout TIMEOUT \
  --output /existing/output/unique-capture.dsl \
  --no-private-decode \
  --json
```

使用 `--no-private-decode` 是因为 gx-private 是同步 clock/data 帧解析器，不是 TXEN/RXEN/sync DebugIO 分析器；关闭它可以避免无关解析失败将成功采集变为 exit 13 partial。

### 可选触发

```text
--trigger none|D<channel>:rising|D<channel>:falling
--pretrigger <0..90>
```

触发通道必须包含在 `--channels` 中。`--timeout` 必须为正数，默认 60 秒，并以一个总 deadline 覆盖等待触发和采集。gx 测试报告已验证 trigger timeout 清理路径，但尚未用受控边沿验收成功触发和 pretrigger；在项目 golden 验证前把边沿触发标为条件能力。

### 与串口刺激配合

1. 确定足以覆盖“启动等待 + 串口命令 + 无线事务”的 samples。
2. 启动前台 capture 进程，并在独立执行通道中保持等待。
3. 根据用户确认的启动等待时间发串口命令；当前 CLI 只在结束时输出最终 JSON，没有已文档化的机器可读 armed 通知。
4. 若已验证边沿触发，可先让 capture 等待 TXEN/RXEN，再发送串口命令。
5. 等待 capture 完成；若需取消，发送 SIGINT/SIGTERM，让 CLI 执行 stop/join/release。不要直接依赖外层强杀作为正常停止方式。

## 采集后检查与导出

先检查元数据和结构：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli \
  inspect /path/to/capture.dsl --json

env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli \
  validate /path/to/capture.dsl --json
```

capture 的最终 JSON 同时返回 requested 与实际 sample count；先保存该 JSON。`inspect` 返回文件实际的 file version、driver、samplerate、sample count、物理通道、通道名、trigger position、block count 和 `complete`，不重新提供请求值。`validate` 严格检查 v2/v3 logic 文件结构，但不验证协议语义或 GUI 渲染。

为大模型或脚本导出 CSV：

```bash
env LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu \
  /home/zhuhy/opt/gx-dsview/bin/gx-dsview-cli export \
  --input /path/to/capture.dsl \
  --format csv \
  --output /path/to/capture.csv \
  --json
```

`--format` 还可取 `vcd` 或 `gnuplot`。保留 `.dsl` 作为原始证据；当前 GUI/CLI 文本导出的逐字节 golden 兼容仍未完成，不把 CLI 自己可读取作为完整 GUI 兼容证明。

## 退出码

| 码 | 类别 | 处理 |
| ---: | --- | --- |
| 0 | success | 继续检查 JSON 与文件 |
| 2 | invalid argument | 修正参数，不重试硬件 |
| 3 | device not found | 重新枚举并确认 device 0 |
| 4 | device busy | 停止并发/GUI 占用后再由用户决定重试 |
| 5 | device open failed | 检查 JSON、stderr、libusb/固件环境 |
| 6 | unsupported configuration | 查询 capabilities 并修正 rate/channel/samples |
| 7 | trigger timeout | 无成功输出；核对触发边沿与 timeout |
| 8 | capture timeout | 核对 samples、rate 与 deadline |
| 9 | disconnected | 标记本次无效，不分析残缺 capture |
| 10 | capture failed/cancelled | 读取 `error.code` 区分取消与失败 |
| 11 | file I/O | 检查父目录、已存在文件、空间与权限 |
| 12 | export failed | 保留 `.dsl`，检查格式和目标路径 |
| 13 | decode failed/partial | 若 `capture_status=success`，保留并校验 `.dsl` |
| 20 | internal error | 保存 JSON、stderr、命令与二进制指纹 |

## 能力边界

- 当前安装二进制尚未在本轮执行真实 devices/capabilities/capture；不要把文档示例输出冒充当前运行结果。
- 物理通道到设备 A/B 与 TXEN/RXEN/sync 的映射仍是每次采集输入。
- 成功边沿触发、pretrigger、USB 拔出、明确 GUI busy 和长时间泄漏尚非完整验收能力。
- DSLogic Plus 冷启动固件资源在 gx 测试文档中仍有风险；不要复制其他型号固件替代。
- 25 MHz 是否能观察约 50 ns sync pulse 必须由 golden capture 验证。
- gx-private 默认配置中的 tx/rx 字段目前不参与算法；不要把其 sidecar 当作 SmartRF DebugIO 分析结果。
