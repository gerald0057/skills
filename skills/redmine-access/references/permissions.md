# 本地权限

只在用户要求查看或调整权限时读取本文件。本地策略只能缩小内置能力，不能增加客户端未实现的端点、方法或字段。

## 调整方式

在本地交互式终端运行：

```text
python3 scripts/configure.py permissions --profile NAME
```

向导会重新验证 API 身份、列出可访问项目，并设置：

- 哪些项目允许写入；
- 哪些写操作可在逐次确认后执行；
- 是否允许私有评论。

API Key 不会回显。不要让用户在聊天中粘贴凭据，也不要直接用 Agent 修改策略来完成当前写请求。

## 权限语义

- 读取操作只能是 `allow` 或 `deny`。
- 写操作只能是 `confirm` 或 `deny`；写入不能设置为无确认执行。
- 删除操作只能是 `deny`，且客户端还会无条件拒绝所有 HTTP DELETE。
- 未声明操作、未知字段、未知 custom field、缺失或损坏的策略全部按拒绝处理。
- `write_projects` 必须逐项列出项目 identifier，不接受通配符；以客户端从 Redmine 重新读取的实际项目为准，不能信任调用者提供的项目名称。

默认策略允许紧凑读取。常规写操作包括：

```text
issue.create
issue.update
issue.comment
time_entry.create
attachment.upload
```

`issue.private_comment` 独立控制。`issue_create_fields`、`issue_update_fields` 和 `custom_field_ids` 进一步限制字段；`max_time_entry_hours`、`max_attachment_bytes` 和 `pending_ttl_seconds` 限制单次影响。

## 服务端权限

本地确认机制不能替代 Redmine 权限。默认读取 profile 应使用只读账号；写 profile 使用非管理员、只拥有必要项目权限的账号。不要使用能够管理用户、项目或模拟其他用户的 API Key。

配置保存在 `~/.config/skills/redmine-access/`，目录为 `0700`、文件为 `0600`。待确认记录和最小审计信息保存在 `${XDG_STATE_HOME:-~/.local/state}/skills/redmine-access/`，避免与长期配置一起备份；审计不包含 API Key、正文或评论内容。
