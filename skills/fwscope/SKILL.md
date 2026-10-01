---
name: fwscope
description: 使用 FwScope 对 ncm2 artifact bundle 或固件 ELF、GNU MAP、最终链接脚本做只读分析，检查容器与产物完整性、RAM/Flash 静态占用、模块与符号排行，以及指定入口的 RAM/XIP/ROM 静态执行路径。用于固件内存定位、版本产物核对、体积排查、RAM-only/XIP 可达性验证和离线 HTML/JSON 报告；不用于运行时性能分析、固件构建或通用反编译。
---

# FwScope

## 先确定分析目标

将请求路由到最小可用分析链：

- RAM/Flash 总量、大模块、大符号、Section、剩余容量：使用 `resources`。
- 函数是否只在 RAM 执行、是否静态可达 XIP/ROM、调用路径或未知控制流：使用 `code-path`。
- 用户提供 ncm2 artifact 目录、`.tar.gz` 或 `.tgz`：使用 `--bundle`，保留 manifest 和 checksum 溯源。
- 需要在浏览器中交互选择产物并切换两类分析：使用 `server`。
- 只需可分享结果：优先生成离线 HTML；自动化消费使用 JSON。

FwScope 分析编译后产物，不负责构建固件、修改 SDK、运行时热点/栈峰值测量、完整数据流分析或固件发布。用户的问题超出这些边界时，明确说明静态证据能回答什么。

## 确认工具和输入

先运行：

```bash
command -v fwscope
fwscope --version
fwscope --help
fwscope resources --help
```

若当前命令的帮助与本 skill 示例不一致，以本机 `--help` 为准并报告版本。使用 bundle 前确认帮助中存在 `--bundle`；该入口从 FwScope 0.1.9 起提供。命令不存在时，先查找用户提供或当前项目中的 FwScope 源码；可在其仓库根目录使用 `python3 -m fwscope`。不要未经用户要求安装、升级或重装工具。

优先保持用户提供的输入形态。ncm2 bundle 已声明成员身份和哈希，存在时不要先手工解压再改用散落文件。没有 bundle 时，分析至少需要 ELF，并优先使用同一次构建产生的 ELF、MAP 和最终预处理链接脚本；不要把历史 ELF 与当前共享 MAP/LD 拼接。用户只给出目录时，先有界查找包含 `manifest.json` 的 artifact 目录和 `.tar.gz`/`.tgz` 候选，再查找 `*.elf`、`*.map`、`map.txt`、`*.ld` 和 `link.ld`。候选不唯一时根据 label、manifest 元数据和构建目录收窄，不能擅自挑选看似最新的一组。

输入能力：

| 输入 | 可用证据 |
|---|---|
| ncm2 artifact 目录或压缩包 | manifest 声明的完整文件组、成员哈希、SDK commit/dirty 状态及可选外层压缩包校验 |
| ELF | Section、符号和部分静态占用；容量、来源或加载位置可能未知 |
| ELF + MAP | 增加目标文件/库归属及 ELF/MAP 核对 |
| ELF + LD | 增加 MEMORY 容量、执行区域和加载映射依据 |
| ELF + MAP + LD | 执行当前支持的完整交叉检查 |

`--bundle` 与显式文件、SDK selector 和 `--layout` 互斥。显式文件模式不会自动补齐同目录 MAP/LD。SDK 自动发现模式需要匹配的 SDK Profile 和 selector；GX83xx 常用 `--sdk-root`、`--app`、`--board`、`--rtos`。FwScope 不会自动识别任意芯片；非内置平台需要对应 Target Profile 和资源映射规则。

## 使用 ncm2 artifact bundle

遇到 artifact 目录或压缩包时，先完整读取 [bundle 输入](references/bundle-inputs.md)。常用调用：

```bash
fwscope resources --bundle /abs/output/artifacts/LABEL.tar.gz

fwscope code-path inspect --bundle /abs/output/artifacts/LABEL.tar.gz

fwscope code-path functions --bundle /abs/output/artifacts/LABEL.tar.gz \
  --query FUNCTION_HINT --limit 50
```

不要自行解压压缩包、改写 manifest/checksum，或在 bundle 校验失败后抽出部分文件继续追求正常结果。bundle `integrity` 只证明成员符合 manifest/checksum 声明；它与 ELF/MAP/LD/Target 的 `artifact_status` 是独立证据，checksum 也不是发布者身份签名。

## 执行资源分析

先得到整体结果，再按问题筛选；不要只凭过滤后的排行推断总量：

```bash
fwscope resources --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld

fwscope resources --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld \
  --memory ram --category data --top 20

fwscope resources --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld \
  --format html --out /abs/fwscope-resources.html

fwscope resources --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld \
  --format json --out /abs/fwscope-resources.json
```

使用 bundle 时，将三个显式输入替换为单个 `--bundle /abs/LABEL` 或 `--bundle /abs/LABEL.tar.gz`，其余输出和展示参数不变。

输出文件使用明确的绝对路径。覆盖现有报告前先确认用户意图；不得让输出覆盖或硬链接到输入产物。JSON 是完整数据，不给它附加 `--memory`、`--category`、`--query` 或 `--top` 等展示筛选。

解释结果时完整读取 [报告语义](references/report-semantics.md) 的“资源报告”部分。先检查 `artifact_status`、`physical_totals_complete`、`warnings` 和映射规则，再引用总量、排行或剩余容量。符号大小不能直接求和代替模块或物理区域总量。

## 执行代码路径分析

按 `inspect → functions → analyze` 逐步建立证据：

```bash
fwscope code-path inspect --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld

fwscope code-path functions --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld \
  --query FUNCTION_HINT --limit 50

fwscope code-path analyze --elf /abs/app.elf \
  --map /abs/map.txt --linker-script /abs/link.ld \
  --entry ACTUAL_FUNCTION --format html --out /abs/fwscope-code-path.html
```

bundle 模式同样按 `inspect → functions → analyze` 执行，只把输入部分替换为 `--bundle /abs/LABEL.tar.gz`。

入口必须来自最终 ELF/dump；不要把源码函数名或示例名直接当作实际入口。函数重名时使用 `functions` 返回的具体指令地址。可重复 `--entry` 分析多个入口，但每个入口的范围独立，不能跨入口累加成固件总量。

代码路径需要匹配架构的 GNU objdump 或受限的预生成 dump。bundle 即使包含经过哈希校验的 disassembly，仍优先从 ELF 使用可用的 live objdump；只使用预生成 dump 时保持证据受限，不能给出 `RAM_ONLY_PROVEN`。找不到工具时可按用户给出的可信路径使用 `--objdump`；不要下载未知二进制或用不匹配架构的工具强行分析。仅当报告明确显示遍历被 `--max-nodes` 截断时才提高上限；增加上限不能消除间接调用、未知区域或不透明指令。

解释结果时完整读取 [报告语义](references/report-semantics.md) 的“代码路径报告”部分。分别报告产物状态、CFG 完整性和区域结论；`XIP_STATICALLY_REACHABLE` 表示存在静态可达路径，不表示运行时必然执行，`RAM_ONLY_PROVEN` 也不证明没有 Flash 数据访问、异步中断或其他任务。

## 使用统一工作台

只有用户希望交互浏览或在同一页面切换资源与路径分析时才启动：

```bash
fwscope server --root /abs/authorized-build-root --port 0 --no-open
```

授权根目录只覆盖本次需要的构建目录。默认保持 loopback，不为方便而暴露到远程网络。受限环境不能使用 loopback 时，改用离线 HTML。工作台读取服务所在机器的文件，不是上传服务；构建产物变化后旧会话会失效，应重新载入而不是继续解释缓存结果。

载入 bundle 时等待工作台完成后台校验，不把 tar.gz 候选发现成功当作已经验证。页面对成员校验显示真实字节进度，对 ELF/MAP/LD 解析等不可量化阶段显示阶段和耗时。tar.gz 只在正式载入时将分析所需成员物化到系统临时目录；会话淘汰或 server 关闭后清理。

## 处理失败与受限结果

- 退出码 `0` 只表示请求完成；资源报告仍可能受限，非 strict 路径报告也可能包含不确定结论。
- 输入或资源对账冲突时停止，不删除某个输入来追求绿色结果；先核对是否同次构建以及 Profile/规则是否匹配。
- bundle 的 manifest、成员大小/SHA-256、`checksums.sha256`、外层校验文件或归档结构校验失败时停止；不要绕过校验或改用已解压的可疑成员。
- 只有 ELF 时可以给部分结论，但把容量、来源和加载位置中的未知值保留为未知；`null` 不是 0。
- 文件在分析期间变化时，停止并建议在构建完成后重试或使用外部准备的稳定产物组。
- LTO、stripped ELF、间接调用或缺少最终 LD 导致的未知不能通过猜测补齐。

## 交付结果

先给一句话结论，再列出：工具版本与完整命令、输入文件及产物状态、关键数值或证据路径、限制/警告、生成报告的绝对路径。bundle 输入还要报告 `label`、`sdk_commit`、`sdk_dirty`、`integrity` 和 `archive_checksum`；`sdk_dirty=true` 是来源事实，不等同于校验失败。区分“工具直接证明”“基于报告的推断”和“仍需验证”。分享 HTML/JSON 前提醒用户其中可能包含本机路径、符号名、库名、哈希、SDK commit 和内存布局。
