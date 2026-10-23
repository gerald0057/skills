# 版本识别与适配

## 找到正确的证据

先确定用户要使用的宿主、插件源码和插件包。优先使用用户指定位置及当前项目；从已确认的工作区内有界查找 `package.json`、`host-descriptor.json`、`packages/plugin-api`、`plugins/` 和 `docs/plugin-contract.md`。不依赖固定的用户目录，也不要求以后能再次读取来源会话。

在确认的宿主源码根目录读取：

```sh
git rev-parse HEAD
git status --short
node --version
cat .nvmrc package.json
rg --files -g 'AGENTS.md' -g 'host-descriptor.json' -g '*manifest*.json' \
  -g '!node_modules' -g '!release' -g '!out' .
```

生成目录可能被 Git 忽略，`rg --files` 没有命中不证明描述不存在；另检查已知的 `dist/host-descriptor.json`。旧版本没有某个路径或命令时，读取其 `package.json`、帮助及实现来寻找等价入口，不套用新版本命令。

| 证据 | 可支持的判断 | 不能推出的结论 |
| --- | --- | --- |
| 运行实例提供的实际 HostDescriptor、兼容详情 | 当前实例声明的 API、服务与策略 | 原生硬件已能正常通信 |
| 与目标构建关联的描述及源码/产物标识 | 该目标的离线兼容评估 | 另一安装包或当前源码具有相同实现 |
| 对应 commit/tag 的源码生成器、适配器、锁文件 | 可以重新构建并核对实现能力 | 工作区中的旧 dist 已经更新 |
| 仅有应用版本、文件名或最新源码 | 定位候选构建 | 已确定运行能力或兼容范围 |

当前基线的描述尚未绑定安装包摘要；commit、生成文件和安装包之间的对应关系需要单独核对，不能宣称已有发布级绑定。开发目标可在对应源码构建后使用新生成描述；诊断正在运行的旧安装包时不能用这份新描述替代它。

## 分开记录各版本

| 维度 | 证据位置 | 适配用途 |
| --- | --- | --- |
| 宿主应用 | `package.json.version`、安装包信息、commit/tag | `requiresHost` / `minHostVersion`，不能代替 API |
| 描述格式 | `descriptorVersion` | 评估器能否理解目标描述 |
| 后台 API | `host.apis.backend`、编译身份 `apiVersions` | `requiresApi` 与实际后台握手 |
| 页面 API | `host.apis.page`、页面编译 SDK | `ui.requiresApi`，独立于后台 |
| 服务 | `host.capabilities`、宿主实际适配器 | 权限和 required/optional 服务范围 |
| 清单 / 包 | `manifestVersions` / `packageFormats` | 能否解析清单与 `.gxplugin` 容器 |
| 运行时协议 | `runtime.protocolVersion`、SDK wire 类型 | HostHello / PluginReady 格式 |
| 插件发布 / 源构建 | `manifest.version`、`runtime.buildId`、编译身份 | 不可覆盖版本、前后台身份一致 |
| 包内容摘要 | 目标打包/inspection 结果 | 内容完整性和实例绑定，不是发布者签名 |
| 数据 / 反向策略 | 实际数据清单、`host.policy` | 读写范围与宿主对已知插件版本的限制 |

用目标仓库自己的 SemVer 评估器，不自行写字符串比较或 `>=` 推断。实现数组是 exact versions，清单中才是范围；`0.x`、预发布、多个 major 的规则交给目标评估器。

## 已观察的实现基线

来源：会话 `01a15e3e-25f9-7ee0-8298-bf88731dd30b`，以及 2026-10-23 只读核对的 GX Workbench commit `468e5a017344946e121e50a659df0db52c4f59c5`。应用版本为 `0.1.0`。下表只用于定位已知接口，不是未来版本支持表；新目标必须重新核对。

| 该基线的功能 | 必须核对的契约 | 对应参考 |
| --- | --- | --- |
| legacy 后台与宿主 PluginView | schema 1，原有 backend API；没有编译身份的明确 legacy 适配 | `plugins/workbench-demo/manifest.json`、`src/backend.ts` |
| 自定义页面、明确启停、停止后读记录 | schema 2 + 生成 runtime 身份；backend `1.1.0`、page `1.1.0` 起，相关权限 | `page-manifest.json`、`src/page-backend.ts`、`src/page.ts` |
| 物理设备租约与命令队列 | 实际 backend `1.2.0` 起，devices/resources `1.1.0`，具体操作适配器 | `packages/plugin-api/src/devices.ts`、`packages/core/src/devices.ts` |
| 页面启动参数、设备列表、实时读取 | page `1.2.0`；传启动参数另需实际 backend `1.3.0`；对应 devices/live 权限 | `packages/plugin-api/src/runtime.ts`、`sdk.ts` |
| 有序请求、live.append、context.finish | 实际 backend `1.3.0`；live 还需服务 `1.0.0` | `packages/plugin-api/src/index.ts`、`live.ts` |
| 原生串口 serial.exchange | 桌面 transport `1.1.0`、device 独占租约、effect=configure | `packages/core/src/serial-host.ts`、`serial.ts`、`plugins/pm-vbat` |

完整桌面描述：backend `1.0.0/1.1.0/1.2.0/1.3.0`，page `1.1.0/1.2.0`；clock/records/live `1.0.0`，devices/resources `1.0.0/1.1.0`，transport `1.0.0/1.1.0`；schema 1/2，容器 `gxplugin/1`，运行时协议 1。

`createHostDescriptor(version)` 是**本份源码实现加给定应用版本**，纯核心不含原生 transport；桌面的 `serialHost(version)` 才补串口服务。两者均不能据此还原历史构建。HID 身份和资源框架不等于已有原生 HID 适配器；此基线尚无该适配器。

## 选择适配方式

- **一个包覆盖多个目标**：实际使用的 API/服务在所有目标上存在共同实现，且编译 SDK 支持每个目标最终协商版本。逐个描述评估并运行验证，不只检查目标中的最高版本。
- **可选能力降级**：业务在能力缺失时仍可完成主要功能，界面能说明限制，代码只在协商成功后调用。不能把串口采集插件的 transport 改成 optional 后继续照常发送。
- **不同插件版本或适配分支**：旧宿主无法提供必要能力时，保留功能受限的旧版本或明确最低目标；不要伪造向后兼容。宿主通用能力扩展属于单独可审查的实现变更。
- **未知目标**：现有评估器不认识新 descriptor/schema/容器时，先找匹配工具链和实现文档。把“工具不支持检查”与“插件已经证明不兼容”分开报告。

应用范围、API 范围、服务范围和反向策略同时成立才可启动；`requiresHost` 与可选的 `minHostVersion` 同时存在时取约束交集。对旧目标不盲目删除上界或使用 `*`。

## 目标源码导航

先读实现契约 `docs/plugin-contract.md`、`docs/compatibility-contract.md`。`docs/version-compatibility.md` 含未来迁移与发布方案，须与实现状态区分。

按症状定位 `packages/plugin-api/src/{manifest,compatibility,runtime,sdk}.ts`、`packages/core/src/{package,registry,core}.ts`、宿主原生适配器及 `apps/desktop/{plugin-runtime,plugin-pages,page-preload}.ts`。命令行为以 `scripts/{check-compatibility,verify-plugin,pack-plugin}.ts`、构建身份以 `scripts/build.mjs` 为准。
