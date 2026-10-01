# FwScope 报告语义

## Bundle 溯源与分析状态

使用 `--bundle` 时，先读取 `provenance.bundle` 或代码路径 `resolution.bundle`：

1. `format`、`schema_version`、`label`、`project` 是否符合预期。
2. `integrity` 是否为 `VERIFIED`。
3. `archive_checksum` 是 `VERIFIED` 还是 `NOT_PROVIDED`；后者表示外层校验文件缺失，不是校验不匹配。
4. `sdk_commit`、`sdk_dirty`、`created_at`、application/board/os 是否对应目标构建。
5. `members` 是否给出本次实际采用的 ELF、MAP、最终 LD、dump 和 info。

这组状态不替代 `artifact_status`。bundle 可以内部完整，但 Target/Profile 错误或 ELF/MAP/LD 证据矛盾；反过来，外层 checksum 未提供也不自动否定已完成的成员交叉检查。checksum 不是数字签名，不能证明发布者身份。

## 资源报告

按以下顺序判断结果是否可用：

1. `schema_version`、`report_type` 与 `tool_version` 是否符合调用方预期。
2. `artifact_status` 是否为 `CONSISTENT` 或 `INSUFFICIENT`。证据冲突通常直接停止，不能保留表面合理的统计。bundle 输入还需单独检查上一节的容器状态。
3. `physical_totals_complete` 是否为真；它与“命令成功”和“已提供全部文件”不是同一个判断。
4. `warnings`、产物 provenance 和 `resource_rules` 是否说明映射、来源归属或容量受限。
5. 确认以上边界后再使用 `physical_regions`、`sections`、模块排行、符号排行和 `heap_layouts`。

关键解释规则：

- 所有大小单位为字节；区间使用 `[start, end)`。
- `null` 表示未知，不是 0。
- 总占用以 Section 投影后的物理区间并集计算。RAM 地址别名、运行地址和加载地址可能指向相同物理存储，不能直接相加。
- 模块来源依赖 MAP 中的目标文件/静态库归属；LTO `.ltrans` 或未归属内容不会凭函数名还原源码模块。
- 符号用于定位和排行。符号可能重叠、互为别名或不能覆盖全部 Section，因此不能求和代替总量。
- RAM 初始化内容还会有 Flash 加载副本；同一内容出现在 RAM 与 Flash 视图中不一定是重复统计错误。
- 堆报告是静态预留布局，不是运行时峰值；静态栈符号已包含在静态占用中，不再重复累加。
- Flash 容量通常取最终 LD 的应用 MEMORY 区域，不自动代表整颗芯片，也不包含 ELF 外的 boot、DFU 包装或镜像尾部。
- 搜索、Top N、资源类别筛选只改变展示，不改变摘要分母和固件整体总量。

报告“校验通过”只说明已执行的产物和映射检查通过，不证明多份文件必然来自同一不可变构建快照。若分析期间仍在编译，先稳定产物再重试。

## 代码路径报告

将三个维度分开解释：

1. 产物一致性：ELF、MAP、最终 LD、Target 和反汇编证据是否一致。
2. CFG 完整性：遍历是否被截断，是否存在间接控制流、未知指令、缺失目标、陷阱或不透明返回。
3. 执行区域：已记录的可达指令落在 RAM、XIP、ROM 或 UNKNOWN 的哪一类区域。

结论语义：

| 结论 | 可证明的内容 | 不能证明的内容 |
|---|---|---|
| `RAM_ONLY_PROVEN` | 在声明的显式控制流边界内，产物一致、CFG 完整且所有可达指令在 RAM | 没有 Flash 数据读取、没有异步中断/其他任务、运行时性能满足要求 |
| `XIP_STATICALLY_REACHABLE` | 保守 CFG 中存在到达 XIP 指令的路径 | 运行时条件一定会走该路径，或路径是热点 |
| `INDETERMINATE` | 当前证据存在未知控制流、区域、指令、返回或遍历截断 | 代码一定进入 XIP，或一定只在 RAM |
| `INVALID_ARTIFACTS` | 输入证据相互矛盾，正常路径结论不可用 | 不能通过忽略冲突文件继续得出结论 |

其它边界：

- CFG 覆盖选定入口及被调函数的已解码普通显式控制流，不覆盖异步中断、其他任务、隐式硬件故障、动态重映射或自修改代码。
- `ecall`、`ebreak`、不透明间接跳转和无法证明安全的返回应保持 UNKNOWN。
- objdump 在间接跳转旁显示的地址注释不是寄存器值证明。
- 数据引用与指令执行区域是两套证据；RAM-only CFG 不等于完全不访问 Flash 数据。
- 多入口报告对每个入口独立应用遍历上限。相同指令可在不同入口结果中重复，不能合并为固件全局执行量。
- 报告中的区域比例是已记录可达指令的构成，不是 RAM/Flash 容量、运行时间或热点比例。
- bundle 中的预生成 disassembly 即使成员哈希已验证，也仍是受限后备证据；检查 `disassembly.source`，不能仅凭 bundle `integrity=VERIFIED` 得出 `RAM_ONLY_PROVEN`。

## 退出码与自动化

| 退出码 | 含义 |
|---|---|
| `0` | 请求完成；报告仍可能受限或不确定 |
| `1` | `code-path analyze --strict` 检出证据不足、不确定或截断 |
| `2` | 参数、加载或解析错误，例如输入冲突或入口未知/有歧义 |

自动化必须检查报告中的状态、完整性和警告，不能只检查退出码。XIP 静态可达本身不是 strict 失败条件；strict 关注证据完整性。
