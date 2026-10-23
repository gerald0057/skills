# 不兼容排查

## 先重现失败阶段

确定失败发生在容器读取、导入保存、选用/注册、页面握手、后台握手、start、设备 I/O 还是数据读取。收集本次实际包、插件 ID/版本、当前选中版本、目标宿主描述及来源、结构化错误和相关日志；不因导入成功就认定已选用或能运行。

基线允许合法但不兼容的候选包保存为“已安装/未启用”。运行中的插件导入新包时也可能只保存候选，当前任务继续使用旧版。先看管理页实际选择及任务状态，不直接改磁盘注册表或安装文件。

在有对应工具链的源码根目录运行，只读评估不会安装或激活插件：

```sh
npm run --silent compatibility -- /abs/plugin.gxplugin --host /abs/target-host-descriptor.json
```

需要检查现有数据时另传真实清单：

```sh
npm run --silent compatibility -- /abs/plugin.gxplugin \
  --host /abs/target-host-descriptor.json --data /abs/data-inventory.json
```

基线退出码：0 为本次已检查约束通过，1 为不兼容或元数据 schema 无效，2 为文件读取、容器 inspection、JSON 语法或调用错误。不要把 2 解释成单纯版本冲突。保留 `issues[].{code,subject,actual,required,message}`、`negotiated`、`unavailableOptionalCapabilities`、`dataChecked`，修完首项后重新评估全部约束。

对未知新格式，旧评估器的拒绝只能说明其识别能力有限；先确认该工具能理解目标，不能先删掉未知字段。目标源码没有 compatibility 命令时，沿其解析器和注册/启动入口定位等价检查，不臆造 CLI。

## 按证据修复

| 错误或现象 | 先核对 | 对应修复 |
| --- | --- | --- |
| 容器 hash、路径或文件检查失败 | 原包是否损坏、缺失入口、路径大小写、打包目录混入历史文件 | 修构建输入并重打包，不手改包或重算摘要掩盖损坏 |
| INVALID_MANIFEST | 使用的 schema、未知字段、权限映射、带 ui 却缺 runtime | 按目标 schema 修清单；源清单未注入 runtime 时检查最终构建输出 |
| INVALID_HOST_DESCRIPTOR | 描述来源、descriptorVersion、exact versions、策略格式 | 找到目标真实描述或匹配评估器，不凭应用版本拼一个 |
| MANIFEST_FORMAT_UNSUPPORTED / PACKAGE_FORMAT_UNSUPPORTED | 目标支持的清单/容器版本 | 使用目标支持且能表达需求的格式；否则明确升级/独立插件版本 |
| HOST_VERSION_UNSUPPORTED | requiresHost 与 minHostVersion 的交集、实际构建是否匹配 | 查明代码确实可兼容后才修范围；缺能力时保留最低要求 |
| API_UNSUPPORTED，subject=apis.backend | 宿主 backend 实现、清单要求、编译 SDK | 采用实际存在的 API 实现或维护适配版本，不能只下调 requiresApi |
| API_UNSUPPORTED，subject=apis.page | ui.requiresApi、page 实现、编译页面 SDK | 分别适配页面方法；后台兼容不代表页面兼容 |
| CAPABILITY_UNAVAILABLE / CAPABILITY_VERSION_UNSUPPORTED | 缺服务还是服务版本不同、是否误用纯核心描述 | 使用实际桌面描述/适配器；有真实降级才设 optional，否则明确最低目标 |
| PLUGIN_VERSION_UNSUPPORTED | 目标 host.policy 对该 ID 的 minPluginVersion/deniedVersions/reason | 修已知问题并发布允许的新版本，不改 ID 或关闭策略绕过 |
| DATA_SCHEMA_UNSUPPORTED / INVALID_DATA_DESCRIPTOR | 真实格式、owner/schemaId/access、reader/writer 范围 | 修读取/写入实现或制定迁移；保留原始数据，评估器不会迁移 |
| HANDSHAKE_MISMATCH | 最终清单与两 bundle 的 ID/版本/buildId/role、SDK API 交集、实例/包摘要 | 同一次构建生成全部身份；从编译 SDK 核对 API，不从 hello 复制身份 |
| HANDSHAKE_TIMEOUT | JS 是否加载、页面相对路径、导出 build、注入 define、模块异常及 IPC 销毁 | 修实际加载或启动异常；基线 10 秒超时通常不是增加等待就能修好 |
| 静态通过、设备方法仍拒绝 | 实际后台握手版本与服务协商、设备租约、操作白名单 | 修 SDK/清单与真实调用；静态最高版本不授予运行时权限 |
| RESOURCE_BUSY | conflict 中设备、资源、holders 和当前任务 | 提示占用与显式重试；不抢占，不当成 SemVer 失败 |
| PERMISSION_DENIED / CAPABILITY_UNAVAILABLE（运行时） | 权限是否声明、optional 是否协商、方法允许的角色 | 修声明和角色边界；页面不能直接使用后台写入服务 |
| BACKPRESSURE，长时间才失败 | 旧请求 4096 次、JSONL 1000 条/8 MiB、队列及在途上限 | 用已实现的有序请求/live，或实现所需存储；不能取消限额掩盖无界增长 |
| TIMEOUT / DISCONNECTED / IO_ERROR | 同参数单次通信、设备占用、驱动/权限、拔线及真实 operation | 与接口不兼容分开处理；停止回收，不自动重发或清空历史 |
| 同 ID/版本“immutable” | 实际安装包与本次生成内容是否不同 | 发布新插件版本，经管理页切换；不覆盖安装目录 |

宿主能力声明不代表此刻有设备：纯核心缺 transport 是描述差异，桌面 transport 可用但设备列表为空是枚举/环境问题；HID 实体适配未实现也不能靠权限声明获得。

## 定位构建与运行时错配

静态兼容通过后仍失败，分别查看后台和页面的编译身份与实际握手日志。宿主静态选择最高可用 API，SDK 只可应答其自身实现与目标的共同版本；例如老 SDK 不能因新宿主增加 1.3 就使用 finish 或有序请求。

不得移除 runtime 退回 legacy、将后端/页面 role 互换、复用旧 instanceId 或关闭发送者检查。只有原本没有 runtime 且没有自定义页面的旧包才走明确 legacy 后台适配；页面始终需要生成身份。

`.gxplugin` 在基线是有规范化摘要的 `gxplugin/1` JSON 容器，不是任意 ZIP。容器摘要用于完整性，宿主提供的 packageDigest 用于实例绑定；它们不证明发布者身份，也不同于源构建 buildId 或整个文件的 SHA-256。

修改构建后先核对生成清单、后台、页面是否来自同一输入，再重新打包和评估。已安装版本不可变，必须提升发布版本才能测试修复；确保管理页选中的确是新版本，保留旧包供回滚。

## 修复验收

至少重跑原失败目标和一个合理对照：例如相同 `0.1.0` 但缺服务的描述、旧 SDK 与新宿主交集、页面 API 独立失败、无权限时在 I/O 前拒绝。用于负向测试的删减描述必须明确标为合成 fixture，不能作为真实历史宿主证据。

根据改动验证启动失败不残留任务/租约、页面刷新不停止任务、更新失败保留旧选择、回滚不破坏原始记录。多目标版本逐个评估，必要时在对应原生宿主运行相同插件包；兼容 JSON 通过本身不证明硬件和数据迁移安全。

报告根因、修改层级、插件新版本、目标描述来源、已执行结果及未验证部分。无法提供必要服务时明确最低目标或功能差异，不能把“取消检查后能启动”当成修复完成。
