# 受控附件下载

只在用户要求下载 Redmine 附件时读取本文件。V1 仅支持 Issue 中的单个附件，不支持任意附件 URL、批量下载、覆盖、重定向或外部对象存储。

## 流程

1. 使用 `issue ISSUE_ID --include attachments` 返回紧凑附件元数据，让用户确认目标附件 ID。
2. 使用显式 profile 准备下载：

   ```text
   python3 scripts/redmine_client.py --profile NAME prepare-download-attachment ISSUE_ID ATTACHMENT_ID
   ```

   默认目录为 `${XDG_DATA_HOME:-~/.local/share}/skills/redmine-access/downloads/<profile>/<issue-id>/`。只有用户明确指定已有绝对目录时才传 `--output-dir`。
3. 原样展示服务器、项目、Issue、附件 ID、文件名、大小、内容类型、目标路径、操作编号和有效期，然后停止。
4. 用户在新回复中明确确认该操作编号后执行：

   ```text
   python3 scripts/redmine_client.py apply ID --confirm ID
   ```

5. 返回最终路径、实际字节数和 SHA-256。用户只要求下载时到此停止，不读取附件内容。

## 边界

- 附件必须来自指定 Issue 的 `attachments` 列表，并且项目在 `attachment_download_projects` 中。
- 客户端只接受与 profile 的 Redmine 地址同源、附件 ID 匹配的 `content_url`，随后重建受控站内 endpoint；API Key 不进入 URL。
- 下载不跟随重定向，按实际流量执行 `max_attachment_download_bytes`，并校验元数据大小。
- 先写权限为 `0600` 的同目录临时文件，成功后以不覆盖方式原子落盘；失败时清理临时文件。
- 文件名经过路径和控制字符检查，最终以 `<attachment-id>-<filename>` 保存。目标已存在时停止，不替换。
- 下载内容、URL、正文和 API Key 不进入审计。审计只保存操作、目标 ID、项目和结果。

旧权限配置没有 `attachment.download` 时默认拒绝。让用户在本地交互式终端运行 `python3 scripts/configure.py permissions --profile NAME` 启用并选择项目；不要通过聊天或当前任务直接修改权限文件。
