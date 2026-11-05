---
name: fwsize-report
description: 构建或核验固件产物，按模块和子模块分析 Flash/RAM 静态占用，生成 Markdown 统计、图形化预览和正式 PDF。用于需要可追溯构建、代码/数据细分和分层资源报告的任务；运行时堆栈峰值测量使用对应调试工具。
---

# 固件 Flash/RAM 占用报告

围绕同一次构建的证据完成统计和报告。每个构建目标独立成稿，项目适配决定构建入口、物理映射和模块分类，通用统计与渲染保持一致。

## 当前阶段

- “先理解”“给方案”：只澄清目标、配置、口径和交付，不执行构建。
- “先构建/确认分类”：完成对应范围，基于实际链接内容提出模块树。
- “先 MD 和样张”：提供精确统计和代表页，等待用户要求的确认。
- 已授权完整报告：完成构建、统计、渲染与验证；已确认分类和样式直接复用。
- 已有产物：校验输入身份后分析，除非要求重建，否则保留原构建配置。

首次新项目先给 MD 与代表页；明确要求一次完成时继续正式报告。确认点依据本次指令与既有授权。板级/配置变更必须重新构建和统计；版式修改复用同一统计 JSON。

## 构建与证据

构建前读 [build-and-artifacts.md](references/build-and-artifacts.md)。读取仓库规范和原生构建文档，自动发现能够确定的信息，只询问实质性缺项。

新构建默认 clean、关闭工程 LTO，保留其他功能配置。确认工具链具体开关并核验实际编译、链接参数及新对象；预编译库单独记录。用户指定发布配置或 LTO 策略时遵从该配置。

clean 前检查删除范围。使用独立构建目录或逐目标快照，保留上一目标及必要历史产物。失败构建不能使用残留 ELF 形成成功报告。容量溢出时保留失败证据；更换板级或改变功能以用户指定、已授权范围为准。

保存 App、Board、配置、revision/dirty、工具链、实际命令、日志和输入哈希。本地封装命令与 SDK 原生入口分别记录；SDK 文档和报告复现命令使用原生入口。`ncm2` 是本地工具，不写入 SDK 使用说明。

## 分析入口

分析前读 [project-profile.md](references/project-profile.md) 和 [accounting.md](references/accounting.md)。模块名称和地址窗口来自实际项目，不以本次 App/SDK 作为通用映射。

1. 有 FwScope 且有匹配 SDK/Target Profile：按本机 `--help` 导出完整 resources JSON；有对应 `fwscope` skill 时遵循其 bundle/证据规范。不对 JSON 使用 Top N/模块筛选。
2. `account_resources.py --resources` 结合项目映射、同批 MAP 和实际 .o/.a 属性统计，恢复非点开头的输入表。支持普通 GNU/BSD 归档、ELF32/ELF64 及两种字节序。
3. 其他后端按契约导出物理区间，使用 `--intervals`。必须先验证物理映射与总量；该入口本身不解析其他厂商 MAP，也不证明相应 SDK 已适配。

```bash
python3 scripts/account_resources.py \
  --resources /absolute/run/resources.json \
  --profile /absolute/run/project-profile.json \
  --map /absolute/run/application.map \
  --out /absolute/run/accounting.json

python3 scripts/account_resources.py \
  --intervals /absolute/run/physical-intervals.json \
  --out /absolute/run/accounting.json
```

脚本路径按本 skill 实际安装目录解析。默认同时生成同名 Markdown，`--markdown` 可指定路径。输出已存在时选择新 build-id 目录；用户授权更新相同结果时传 `--overwrite`。输出不能覆盖输入或其硬链接。

## 统计不变量

- 内部使用整数 B，KiB 仅用于展示。
- 依据输入段执行属性和物理区间，保留 code/data/reserved/padding/unknown。
- Flash/RAM 分别核算加载副本和运行占用；同一物理区域的别名/共享内容只计一次。
- 静态栈转移归属时从原节点移出；动态分配配置量、堆布局与静态占用分别处理。
- 叶子互斥、父节点由后代汇总；区域、段、模块总量必须对账。
- 模块未归属和类型未知分别保留；null 不能写成 0。
- 分类覆盖实际链接内容；HAL、协议、业务分支及静态栈的具体层级属于项目配置。

先检查 MD，必要时结合源码修正映射。冻结统计 JSON/哈希后再渲染。证据冲突或总量错误先修复输入，不能隐藏矛盾。

## PDF 与验证

生成前读 [report-style.md](references/report-style.md)。环境有 PDF skill 时遵循其生成/交付约定，包括存在时的 artifact operation 标记；不复制环境专有缓存路径。

Python 3.11+；统计核心仅标准库。PDF 另需 WeasyPrint 和可用中文宋体风格字体；PDF 验证使用 Poppler 的 pdfinfo/pdffonts/pdftotext/pdftoppm。优先复用已有运行时；缺依赖时报告实际缺项并保留统计成果。

```bash
python3 scripts/render_report.py --input /absolute/run/accounting.json \
  --preview 3 --out /absolute/run/style-preview.pdf

python3 scripts/render_report.py --input /absolute/run/accounting.json \
  --out /absolute/run/report.pdf

python3 scripts/validate_report.py --input /absolute/run/accounting.json \
  --pdf /absolute/run/report.pdf --render-dir /absolute/run/page-renders \
  --out /absolute/run/validation.json
```

预览和终版共用 [report-template.html](assets/report-template.html) 和渲染器；预览标识放文件名/交付说明。默认 App 页眉、页码页脚、宋体风格、黑白线条。每个目标独立 PDF；Flash 仅总量，RAM 区分代码、数据、预留/填充，确有未知类型时单独显示。

RAM 保留面积图和带百分比纵向辅助线的条形排名。右侧固定列显示两行数值：上行为总占比、合计 KiB，下行为代码、数据、预留/填充 KiB；类别名称集中在表头，数字右对齐。模块内收紧、模块间留白，模块名和条形与两行组垂直居中；不增加横向表格分隔线。长名称及续页规则见版式规范。

面积图按实际字节占比，降序排名保留所有非零子项。总览以应用已用量为分母，展开页以父模块已用量为分母；容量使用率另列。RAM/Flash 分别独立排序。

同一块内两栏共用说明行、面积图上下边界和排名起点。紧凑明细也采用上方面积图、下方排名；统一预留图例与表头高度，保持 RAM 两行分组间距。排名末行按条目数自然结束，不拉大间距凑齐底边。审计同时核对 SVG 内部边界和实际渲染的图形位置。

Flash/RAM 独立均衡分块，续页只绘制尚未结束的排名；明细与附录按渲染器实测高度组合。预览在完整分页后截取，审计保存实际绘制条目、块边界、运行时版本及脚本/模板哈希。使用验证器检查空图、漏项/重复、孤立尾行、越界和明显文字相交；具体规则与回归范围见版式规范。

检查最新渲染的所有页面，修复内部遮挡、越界、密度、分页和字体。自动检查不能代替目视。看过最新渲染后可再次调用验证器加 `--visual-reviewed` 记录事实。预览验证显式传 `--allow-preview`，不能将预览当正式报告。

## 交付

交付当前目标 PDF 和需要的 MD，简述关键数值及实际验证结果。真实范围、分母和确有缺项以事实呈现；提示词、审批过程、样张标记、制作说明和泛化免责声明不进入正式报告。

保留构建证据、统计、渲染 audit 与验证记录的关联。只更新指定目标，复用前核对构建身份，不能仅替换板级文字。
