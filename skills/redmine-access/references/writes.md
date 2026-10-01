# 写操作

只在当前任务确实需要写入 Redmine 时读取本文件。所有 `prepare-*` 都只生成一次性预览，不会立即写入；必须将预览展示给用户并等待其明确确认操作编号。

客户端路径以下用 `CLIENT` 表示：

```text
python3 scripts/redmine_client.py
```

写操作必须在 `CLIENT` 后、子命令前显式添加 `--profile NAME`。Payload 文件应放在仓库外的临时目录；用完后清理，不把 Issue 正文或评论提交到仓库。

## 创建 Issue

Payload 是 Issue 字段本身，不要再包一层 `issue`：

```json
{
  "project_id": "firmware",
  "subject": "连接恢复后统计未刷新",
  "description": "复现步骤……",
  "tracker_id": 1,
  "priority_id": 2,
  "assigned_to_id": 7,
  "due_date": "2026-08-30",
  "estimated_hours": 4
}
```

```text
CLIENT --profile NAME prepare-create-issue --payload-file /tmp/issue.json
```

实际允许字段取决于 `issue_create_fields`。至少需要 `project_id` 和非空 `subject`。

## 更新 Issue

Payload 只放实际变化字段，不回写完整 Issue，也不能移动项目：

```json
{
  "status_id": 2,
  "assigned_to_id": 7,
  "due_date": "2026-08-30"
}
```

```text
CLIENT --profile NAME prepare-update-issue 1234 --payload-file /tmp/update.json
```

客户端会将字段与 `issue_update_fields` 取交集，并在准备和执行阶段读取 Issue 的实际项目。`custom_fields` 中每个 ID 还必须列入 `custom_field_ids`。

## 添加评论

评论正文使用 UTF-8 文本文件：

```text
CLIENT --profile NAME prepare-comment 1234 --notes-file /tmp/notes.txt
```

私有评论还需独立的 `issue.private_comment` 权限：

```text
CLIENT --profile NAME prepare-comment 1234 --notes-file /tmp/notes.txt --private
```

不要用 Issue 更新 payload 夹带 `notes`。

## 登记工时

必须且只能提供 `issue_id` 或 `project_id`；禁止提供 `user_id`：

```json
{
  "issue_id": 1234,
  "hours": 2.5,
  "activity_id": 9,
  "spent_on": "2026-08-20",
  "comments": "分析连接恢复日志"
}
```

```text
CLIENT --profile NAME prepare-time-entry --payload-file /tmp/time-entry.json
```

`comments` 最多 255 字符，`hours` 不能超过权限中的 `max_time_entry_hours`。

## 上传附件

```text
CLIENT --profile NAME prepare-attachment 1234 /absolute/path/report.txt --description "诊断报告"
```

预览会绑定文件名、大小和 SHA-256。确认前文件发生变化时操作失效。附件需要“上传文件”和“关联 Issue”两个写请求；若返回 `INDETERMINATE`，可能已经留下未关联的上传 token，只能读取核对，不能重试。

## 发布多个附件和说明

同一 Issue 需要上传多个附件，或需要将多个附件与一次完整 description 更新作为一个目的发布时，不要创建多个 `prepare-attachment`。使用一个语义化操作包：

```text
CLIENT --profile NAME prepare-publish 1234 \
  --attachment /absolute/path/run.png \
  --attachment /absolute/path/stop.png \
  --description-file /absolute/path/description.textile
```

`--attachment` 可重复；顺序就是上传顺序。`--description-file` 可省略；提供时会以 UTF-8 文件的完整内容替换 Issue description，而不是追加评论。预览必须展示每个文件的绝对路径、文件名、大小和 SHA-256，以及附件总数、总大小和 description 摘要。

操作包只能包含同一 Issue 的文件，文件名必须互不相同且不能与 Issue 现有附件重名。客户端逐个串行上传文件；全部获得 token 后再次检查 Issue 版本，再用一次 Issue `PUT` 同时关联全部附件并更新 description。用户只确认一次，执行期间不再逐项询问。

此流程不是数据库事务。任一上传、版本复查、最终更新或写后验证异常时立即停止且不自动重试；若已经获得上传 token，返回 `INDETERMINATE`，报告可能存在未关联上传并要求先读取 Issue 核对。不要改用多个旧操作编号继续剩余步骤。

## 执行已确认操作

用户在预览后的新回复中明确批准操作编号后：

```text
CLIENT apply chg-1a2b3c4d5e6f789001122334455667788 --confirm chg-1a2b3c4d5e6f789001122334455667788
```

客户端会再次核对服务器、权限、项目、payload 或操作包摘要、有效期和目标 `updated_on`，消费一次性操作记录，再发送请求。写后重新读取资源验证实际结果。`updated_on` 检查只是 Redmine 能力范围内的过期保护，不是数据库级原子锁。
