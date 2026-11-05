# 项目适配与数据契约

## FwScope 输入

需要 schema 1 完整资源 JSON、与 provenance.map.sha256 一致的 GNU MAP、项目配置及必要同批 .o/.a。要求 CONSISTENT、physical_totals_complete=true；bundle integrity=VERIFIED，archive_checksum=VERIFIED 或确实未提供的 NOT_PROVIDED。

从 [profile.example.json](../assets/profile.example.json) 复制到分析目录，填写实际配置。相对路径以配置文件目录为准；不自动展开环境变量或执行 shell。

| 字段 | 语义 |
|---|---|
| schema_version | 1 |
| target | app 必填，id/board/variant/build_id 为实际身份；与 bundle 的 app/board 矛盾时拒绝 |
| object_roots | 允许读取构建对象和库的根目录，包含必要工具链库目录 |
| path_maps | MAP 路径前缀到快照根的映射，最长前缀优先；resolve 后仍在允许的根内 |
| module_rules | 按顺序对归一化来源做正则匹配；path 数组中的 `{0}` 引用第一个捕获组，`{name}` 引用命名组 |
| stack_symbols / stack_path | 转移归属的静态栈符号和目的路径 |
| reserved_sections | 实际预留输出段名到模块路径的映射 |
| direct_child_name | 直接内容与后代共存时的子项名，默认 self |
| build | native_commands 为 SDK 原生命令的 argv 数组；其余记录实际工具链/LTO 检查等 |
| sdk | 实际版本/commit/dirty；bundle 身份优先 |

来源路径去掉前导 `./` 并合并重复斜线，保留 `.a(member.o)` 身份。普通 GNU/BSD 归档和长成员名可读；thin archive 需构建侧提供物化对象或另一个经验证的适配器。

## 通用区间输入

其他后端生成以下 JSON 供 `--intervals` 使用。该入口接收物理数据，不猜地址窗口，也不解析任意厂商 MAP。

```json
{
  "schema_version": 1,
  "target": {"app": "sensor_hub", "board": "evaluation_board", "build_id": "build-001"},
  "build": {"native_commands": [["make", "clean"], ["make", "LTO=0"]]},
  "sdk": {},
  "evidence": {"backend": "project-adapter", "physical_totals_complete": true},
  "regions": [{"id": "ram0", "kind": "ram", "capacity": 1024,
               "capacity_ranges": [[0, 1024]], "expected_occupied": 32,
               "coverage": [[0, 32]]}],
  "contributions": [{"region": "ram0", "start": 0, "size": 32,
                     "kind": "data", "path": ["application", "state"],
                     "source": "src/state.o", "section": ".bss"}],
  "stacks": [], "heaps": []
}
```

- start/size 是物理坐标和 B，允许整数或 0x 字符串；coverage/capacity_ranges 是左闭右开区间。
- region kind 为 flash/ram。其他资源由适配器明确选择范围，独立物理设备用不同 id。
- coverage 来自已核验段投影，expected_occupied 来自独立物理总量；不从 Top N 或已归属符号推断覆盖。
- evidence.physical_totals_complete 必须为布尔 true，表示后端已验证物理覆盖完整；缺失或 false 时此 CLI 拒绝生成正常统计，保留原阶段证据。
- contributions 可重叠；同源重复去重，不同来源共享一次。类型为 code/data/reserved/padding/unknown。
- capacity 未知用 null，expected_occupied 必须是可对账整数。未提供 capacity_ranges 时仅核对容量大小，地址边界由后端证据负责。
- stacks/heaps 是补充布局，不自动增加总量；贡献中已包含需要计入静态占用的字节。

## 统计输出

schema_version=1、report_type=fwsize-accounting。包含 target/build/sdk/evidence、regions、totals、capacities、互斥 leaves、聚合 nodes、sections、stacks、heaps。

节点的 flash/ram 各含五种类型。sections 按物理去重后的段归属汇总；真正跨段共享的区间单列 `<shared sections>`。

渲染器消费该结构，不依赖 SDK 路径。物理映射和文件解析的支持范围分别声明，新增 SDK 用真实产物验证后再扩大支持声明。
