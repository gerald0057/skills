# ncm2 Artifact Bundle 输入

只在输入是包含 `manifest.json` 的 artifact 目录、`.tar.gz` 或 `.tgz` 时读取本参考。

## 选择输入模式

FwScope 0.1.9 起支持 ncm2 schema version 1 bundle：

```bash
fwscope resources --bundle /absolute/artifacts/LABEL
fwscope resources --bundle /absolute/artifacts/LABEL.tar.gz

fwscope code-path inspect --bundle /absolute/artifacts/LABEL.tar.gz
fwscope code-path functions --bundle /absolute/artifacts/LABEL.tar.gz \
  --query main --limit 50
fwscope code-path analyze --bundle /absolute/artifacts/LABEL.tar.gz \
  --entry main --format html --out /absolute/code-path.html
```

`--bundle` 与 `--elf`、`--map`、`--linker-script`、`--dump`、`--info`、SDK selector 和 `--layout` 互斥。不要从 bundle 中抽取几个文件后与当前 SDK 的共享 MAP/LD 混用。

工具按 manifest 中的 artifact `type` 选择 ELF、MAP、最终 LD、disassembly 和 elf-info，不依赖内部文件名猜测。manifest 的 `project` 应匹配所选 SDK Profile；不匹配时要求用户选择或提供正确 Profile，不能仅凭 board/label 猜 Target。

## 校验与拒绝条件

正式载入时应确认：

1. manifest schema 和成员声明可解析。
2. 每个声明成员的大小与 SHA-256 匹配。
3. `checksums.sha256` 与 manifest 完全一致。
4. 若压缩包旁存在同名 `.tar.gz.sha256`，外层压缩包校验通过；缺失时保留 `NOT_PROVIDED`。
5. tar.gz 只有一个与 manifest label 一致的顶层目录。
6. 没有路径穿越、绝对路径、重复成员、链接、特殊文件或超限成员/展开量。

任何一项失败都停止。不要自行修复 manifest、忽略多余成员、放宽资源限制，或将已解压成员当作可信显式输入继续分析。

报告中的证据分层：

- `provenance.bundle.integrity=VERIFIED`：bundle 内部成员符合 manifest 和 checksum 声明。
- `provenance.bundle.archive_checksum=VERIFIED`：外层 `.tar.gz.sha256` 存在且匹配。
- `archive_checksum=NOT_PROVIDED`：没有外层校验文件，不等同于 mismatch。
- `artifact_status`：ELF、MAP、最终 LD、Target 和反汇编等分析证据的交叉检查状态。

成员 checksum 只能检测内容是否符合声明，不能证明发布者身份。`sdk_dirty=true` 记录生成 bundle 时 SDK 工作树不干净，是溯源事实，不应单独改写为完整性失败。

## 压缩包物化与生命周期

不要手工解压 tar.gz。FwScope 会流式读取并校验所有 manifest 成员，只把分析需要的 ELF、MAP、最终 LD、disassembly 和 elf-info 写入私有系统临时目录：

- CLI：调用结束或失败后删除。
- 工作台：保留到会话淘汰、载入失败或 server 关闭。
- artifact 目录输入：直接读取原文件，不复制到临时目录。

原始 tar.gz 和 images/配置等非分析成员不会被完整复制。候选发现只用于列出 tar.gz；完整 manifest、外层 checksum 和成员校验发生在正式载入阶段。

不要删除仍被运行中工作台会话使用的 `fwscope-bundle-*` 临时目录。若怀疑泄漏，先确认对应 server 已结束，再诊断生命周期；不要仅凭目录存在就清理。

## 代码路径证据

bundle 中的预生成 disassembly 与 ELF 同属一个已校验容器，但仍不是 live objdump 的等价替代：

- 有匹配架构的 objdump：从 bundle ELF 重新反汇编，可继续进行完整产物交叉检查。
- 没有可用 objdump：允许回退到 bundle disassembly，但保持证据 `INSUFFICIENT`，不得给出 `RAM_ONLY_PROVEN`。

检查 `disassembly.source` 和 `fallback_reason`，不要因为 `integrity=VERIFIED` 就提升 CFG 证据等级。

## 工作台等待与进度

`fwscope server` 的 bundle 载入和资源分析在本地后台任务中执行。页面会显示：

- 外层校验、manifest、成员校验、准备分析文件等阶段；
- 成员校验的真实已处理/总字节；
- ELF/MAP/LD 解析等不可量化阶段的动画、阶段名和耗时。

不要把不可量化阶段要求成伪百分比，也不要因 tar.gz 候选出现得很快就声称完整性已验证。内部工作台任务 API 不是稳定的自动化接口；自动化仍使用 CLI JSON 输出。
