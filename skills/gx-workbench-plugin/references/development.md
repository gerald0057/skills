# 开发与验证

## 选择最接近的现有示例

先完成目标能力识别，再从目标版本源码中复用必要部分：

| 需求 | 已观察基线的起点 | 注意 |
| --- | --- | --- |
| 自定义页面和少量记录 | `plugins/workbench-demo/page-manifest.json`、`src/page-backend.ts`、`src/page.ts`、`ui/` | 同目录的 `manifest.json` 和 `src/backend.ts` 是 legacy 示例，不能混用 |
| 串口参数、单次/连续采集、实时图 | `plugins/pm-vbat/manifest.json`、`src/{backend,page,protocol}.ts`、`ui/` | 替换业务协议，保留身份、资源、取消和有界缓存机制 |
| 明确要求兼容旧后台宿主 | `plugins/workbench-demo/manifest.json`、`src/backend.ts` | 使用旧能力与宿主 PluginView，不承诺自定义页面 |

新增目录使用目标仓库现有 `plugins/<name>/` 约定；选择唯一插件 ID 和独立版本。独立插件仓库使用目标版本 SDK 和可追溯的构建工具，不能把宿主私有目录结构当作已发布的 npm SDK。

在基线构建中，插件列表是 `scripts/build.mjs` 的显式数组；复制目录不会自动加入构建。添加该插件条目、必要源文件和资产，保持改动只涉及本次插件及必要宿主入口，不重做全套脚手架。

## 清单和构建身份

新插件在目标支持时使用 schema 2：`requiresHost`、`requiresApi`、`ui.requiresApi` 和服务范围分别表达应用、后台、页面与能力要求。`capabilities` 是权限声明；required/optional 映射的键必须在其中，且不能重叠。没有出现在映射里的权限默认仍是必需的基础 `^1.0.0` 服务，不会自动成为 optional。

`backend`、页面入口和图标都是包内可移植的相对路径。`resources` 只声明插件所需资源名，不能替代运行中的租约申请。`protocols` 是业务协议声明，不会自动生成设备适配器。

带 `ui` 的**最终清单**必须有 `runtime: {protocolVersion: 1, buildId}`。基线源清单刻意不手写 runtime：构建生成同一个源指纹并注入最终清单与前后台 bundle，因此直接拿这种源清单做兼容检查会缺失 runtime。优先评估最终包或生成后的清单。

参考目标构建器完成：

1. 将插件源码、实际参与的 SDK/共享模块、资产、构建配置和锁文件纳入源构建指纹，新增依赖模块也要覆盖；不复用上一构建输出。
2. 从同一源清单生成 ID、版本和 buildId；按角色分别注入 `__GX_PLUGIN_BUILD__`，其中 `apiVersions` 来自该份**编译 SDK 真正实现**的精确版本。相同源码树构建可复用其受核对的定义；外部 SDK 不能直接照抄新宿主的版本数组。
3. 后台导出 `build = getPluginBuild()` 和 `activate: ActivatePlugin`；页面用 `connectPluginPage(getPluginBuild())` 获取上下文。前后台身份来自各自编译 bundle，不从 HostHello 拼装。
4. 按目标要求编译浏览器 ESM；此基线使用 `platform: browser`、`format: esm`、ES2022。不导入 Node/Electron/native 模块，不依赖平台路径。
5. 输出 `manifest.json`、后台 JS、HTML/CSS/页面 JS 及实际使用的资产，再使用官方仓库打包入口。构建指纹与最终包摘要各自独立，不能把包摘要嵌进自身。

身份不一致时从同一输入重建两端及清单，不能只改输出 JSON。不要把源码、测试、node_modules 或无关历史输出一起打包。

## 后台、页面和请求

后台 `activate(context)` 返回目标 `PluginBackend` 生命周期；在 `start(options?)` 中先验证业务参数，再申请租约和打开设备。`stop()` 取消定时器/等待并释放租约，容忍启动部分成功和任务已经结束。调用返回 `Result` 时检查 `ok`，IPC 消失引发的 Promise rejection 也要处理。

页面握手后调用本插件的 `state/start/stop`、允许的设备枚举及历史/实时读取；页面没有 `transport.request`、`records.append` 或租约写入入口。`onState` 是生命周期通知，不是样本流；实时样本按任务 ID 和游标读取。销毁页面清理页面订阅/轮询，不隐式调用 stop。

在此基线中，页面 API 1.1 不支持带 options 启动、devices 或 readLive；这些需要 page 1.2，带 options 启动另需实际 backend 1.3。不能因为 SDK 类型中有方法就跳过运行时版本限制。

长期采集使用目标已实现的有序请求模式：从本任务首个请求开始固定采用严格递增的安全正整数 `sequence` 和 `id: seq:<sequence>`，同一任务不切换模式、不复用序号。每次任务从自身序列开始，重试不能复用执行过的请求；请求超时不自动重发业务命令。旧模式每任务 4096 个唯一请求，不是无限任务接口。

API 1.3 的 `context.finish(error?)` 可完成单次任务或报告异步采集失败，让宿主回收资源；只在最终协商了该 API 时使用。停止后迟到结果不继续写数据或安排下一轮。异步定时任务保持至多一个采集请求，错过周期不堆积补发。

## 设备、串口与数据选择

设备操作先取得宿主规范 deviceId，再申请匹配租约。需要多个资源时优先使用已提供的原子 acquireMany，避免部分占用；冲突保留 `RESOURCE_BUSY` 详情。设备命令的真实资源域/副作用由可信适配器决定，插件自报 read 不能使配置或清零成为共享读取。

当前 `serial.exchange` 的请求参数是 deviceId、leaseId、operation、effect、timeoutMs、payloadBase64；operation 固定 `serial.exchange`，effect 固定 `configure`，持有 `device` 域独占租约。payload 是以下业务参数的 UTF-8 JSON 再转 base64：

| 字段 | 基线约定 |
| --- | --- |
| baudRate | 1200～4000000；驱动和固件仍需实际支持 |
| command | 1～256 可打印 ASCII，不包含终端提示符或命令换行 |
| lineEnding | CR、LF、CRLF，由宿主追加 |
| responseMatch | 1～128 字符字面匹配，不是正则表达式 |

串口固定 8N1、无流控；改变波特率要停止后重启。宿主拼接 CR/LF 完整行、跳过回显及不匹配行，返回首个匹配行；插件仍需严格解析业务值。宿主 timeoutMs 范围 1～30000，含排队，PM VBAT 的页面范围更窄，不能把业务 UI 限制当通用接口上限。

PM VBAT 示例发送 `pm_vbat` 加所选换行，不发送 `msh >` 或字面反斜线；`vbat input: 4724 (mV)` 提取为 4724 mV。保留原始行，日志时间前缀不当作已确认的设备时钟。其他插件替换命令/解析，不把该业务复制到通用串口服务。

按真实需求选择记录通道：

- `live.append` / 页面 `readLive`：最近任务 600 条内存样本，分页上限 200；停止后可读，新任务及进程重启清空。跟踪 taskId、sequence 和 dropped，显示丢弃情况。
- `records.append/read`：基线每插件最多 1000 条或 8 MiB，按插件隔离的 JSONL 持久化。达到上限报 BACKPRESSURE，不能默默覆盖或当无限录制使用。
- 长时间完整录制：先确认目标有相应存储服务；基线尚未实现完整分卷/索引/迁移流程，需要明确范围的宿主实现，不能用图表环形缓存冒充。

## 构建和检查

先核对目标 `.nvmrc`、`engines`、锁文件与 `package.json.scripts`。来源基线要求 Node 22.12～23 之前，并固定 `.nvmrc` 和锁文件；后续版本以各自要求为准。依赖已齐全时直接复用，确需安装才在开发工作区按锁文件运行 `npm ci`，不为兼容问题擅自升级依赖。

基线在宿主源码根目录提供：

```sh
npm run check
npm run plugin:pack -- out/my-plugin out/plugins/my-plugin.gxplugin
npm run --silent compatibility -- out/plugins/my-plugin.gxplugin --host /abs/target-host-descriptor.json
npm run smoke
```

`out/my-plugin` 必须已经包含同一次构建的最终清单和 bundle；plugin:pack 只收集文件与打包，不编译。已在 build 中打包时不用再重复执行 pack。多目标逐个显式指定 `--host`，不因当前源码默认检查通过就推断旧目标通过。

`npm run plugin:verify -- <包>` 在基线中使用**当前源码的桌面描述**完成包校验和兼容检查，没有 `--host` 参数；为较新宿主构建的包可能被它拒绝。跨版本优先用 `compatibility -- <包> --host <目标>`，该入口也先检查容器完整性。退出码及数据检查见排查参考。

测试围绕实际业务和故障：解析有效/噪声/截断输入、启动参数校验、页面与任务分离、资源冲突、超时/拔线、停止和迟到结果、多次启停；长采集检查超过旧请求上限及缓存滚动。使用现有测试目录约定。基线 `npm test` 显式枚举测试文件，新增业务测试要接入列表或明确单独执行，文件存在不等于已经运行。

基线 smoke 默认覆盖内置示例和 PM VBAT，不会自动覆盖新增插件。需要时给现有 fixtures/流程增加本插件最小验证，或在测试宿主中显式安装操作，并记录覆盖范围。不要把原有 smoke 通过写成新插件已验收。

同一 `.gxplugin` 只构建一次，保留文件 SHA-256，在 Windows/Ubuntu 原生宿主验证相同字节；不分别重新编译后宣称是同一包。静态兼容、模拟/PTY、目标硬件、源码宿主、打包宿主分别记录。设备参数由用户明确选择，不能枚举端口后自动发送探测命令。

只改插件时通常不需要重打宿主安装包；仅宿主能力或打包变化时按目标脚本完成相应安装包验证。交付插件新版本，保留可回滚的旧包和旧数据，运行任务结束后再通过管理页切换。
