# 信息查询

以下为 `python3 <skill>/scripts/gitlab_client.py` 后的参数。`--project`、`--config` 是全局参数，放在子命令前。没有默认项目：项目级查询必须每次显式指定目标；`status`、`projects` 不接受 `--project`，`doctor` 支持可选项目。

| 请求 | 命令示例 | 结果范围 |
| --- | --- | --- |
| 当前连接/身份 | `status` | 只读身份与服务端版本，不访问项目 |
| 实例访问检查 | `doctor` | 与实例检查相同，不枚举项目或查询流水线 |
| 项目访问检查 | `--project group/project doctor` | 身份、指定项目和该项目的流水线读取权限 |
| 项目搜索 | `projects --search demo --limit 20` | 默认只查成员项目的一页摘要 |
| 其他可访问项目 | `projects --search demo --all-accessible` | 仅用户确需查找非成员项目时使用 |
| 项目详情 | `--project group/project project` | 默认分支、可见性、归档标记、克隆链接等 |
| 分支/标签 | `--project group/project branches`、`--project group/project tags` | 一页摘要及对应提交；均可 `--search` |
| 指定分支/标签 | `--project group/project branch --name feature/demo`、`--project group/project tag --name v1.0.0` | 精确名称；不要混淆同名分支与标签 |
| 提交列表/详情 | `--project group/project commits --ref main`、`--project group/project commit --sha <完整SHA>` | 摘要/指定提交信息；不下载源码或 diff |
| Issue | `--project group/project issues --state opened --search bug`、`--project group/project issue --iid 12` | 列表不输出正文，指定详情才读取正文 |
| Merge Request | `--project group/project mrs --state opened`、`--project group/project mr --iid 12` | 列表/指定详情与已有 head pipeline 状态，不触发重算 |
| Release | `--project group/project releases`、`--project group/project release --tag v1.0.0` | 指定详情含发布说明和资产链接；不下载或执行 |
| Package Registry | `--project group/project packages --name app` | 包名、版本、类型和状态的一页摘要 |
| 包文件元信息 | `--project group/project package-files --id 123` | 文件名、大小和服务端 SHA256；不下载文件 |

`projects`、`branches`、`tags`、`commits`、`issues`、`mrs`、`releases`、`packages`、`package-files` 支持 `--page N --limit N`。每次只读取一页（上限 100），返回 `next_page` 和分页头是否存在；没有头时不能断言没有下一页。用户要完整集合时再逐页查，保留已查范围。

`projects` 仅在用户主动查找项目时使用；不得为缺少目标的其他命令自动搜索并选中首项。未明确项目时先询问。只有用户明确说“当前项目”时，skill 才读取当前 Git remote、核对实例，然后显式传参；脚本不会自行读取 remote 或复用历史项目。

## 输出与隐私边界

查询成功时 stdout 输出 JSON；`trace` 输出带行号的文本，`configure`/`login` 输出本地操作提示。警告和错误写入 stderr。退出码 `0` 表示本次查询或本地操作完成，`2` 表示参数、配置、网络或 API 错误；流水线状态为 `failed` 的查询也可以返回 `0`，必须检查 JSON 中的 `status`，不能把命令成功当作 CI 成功。

脱敏会处理当前 Token、常见 Token 前缀、URL 内的账号密码、Authorization 头，以及普通/带引号的 `password`、`api_key` 等赋值。它不是完整的秘密检测器或匿名化器：查询结果仍可能包含用户名、提交作者名、项目地址及用户请求的正文。对外分享报告前需要再次检查；不要自动导出完整响应或日志。

参数帮助可随时查询，不读取配置、不访问网络：

```sh
python3 <skill>/scripts/gitlab_client.py --help
python3 <skill>/scripts/gitlab_client.py projects --help
python3 <skill>/scripts/gitlab_client.py trace --help
```

## 解释结果

- `project.id` 与 Issue/MR 的 `iid` 不同；用户提供 `.../-/issues/12` 时用 `--iid 12`。
- `merge_status`/`detailed_merge_status` 可能是缓存、检查中或未知，不等于已批准或可安全合并。
- `status` 成功与所有项目可访问不是同一件事。服务端版本接口失败会明确返回 unavailable，而非推测版本。
- 项目摘要不输出 CI 变量、Token、完整成员信息；用户/提交摘要不输出邮箱。
- `release`/`package-files` 只证实服务端元信息存在。文件是否能下载、校验是否匹配、安装是否成功需另行验证。
- 输出中的链接不授权跨实例请求；不要把当前 Token 转发给资产域名。

API 依据（所用查询均为 GET）：

- [实例元信息](https://docs.gitlab.com/api/metadata/)、[Projects](https://docs.gitlab.com/api/projects/)。
- [Branches](https://docs.gitlab.com/api/branches/)、[Tags](https://docs.gitlab.com/api/tags/)、[Commits](https://docs.gitlab.com/api/commits/)。
- [Issues](https://docs.gitlab.com/api/issues/)、[Merge requests](https://docs.gitlab.com/api/merge_requests/)。
- [Releases](https://docs.gitlab.com/api/releases/)、[Packages](https://docs.gitlab.com/api/packages/)。
