# CI 验证与失败定位

固定本次实例、项目和提交，不把 push 成功、本地测试成功或最新一条历史记录当成目标 CI 成功。没有默认项目，不沿用旧配置或上次任务的仓库。只有用户明确指向“当前项目”时，skill 才从当前 Git remote 推导候选；实例不匹配或目标有歧义时先询问。每个 CI 命令都显式传入 `--project`。

1. 读取用户指定仓库的实际分支和完整 SHA；未指定提交时可用 `git rev-parse HEAD`。不要自动 push 或创建空提交来“验证”。
2. 查询匹配 SHA/ref 的候选，区分 push、MR、tag、schedule。无候选可能是未触发、被 rules 跳过或参数不匹配，不能直接说成功/失败。
3. 固定流水线 ID，检查完整作业集合。矩阵项分别检查；失败允许项、手动项、跳过项、重试和下游状态不能省略。
4. 先读失败作业日志末尾，必要时增大范围找首个因果错误；结合该 SHA 的 CI 配置和脚本，而非只看当前 HEAD。
5. 根据证据区分代码错误、依赖下载、镜像/apt、缓存、认证、protected 变量、Runner 限制和发布失败。主机网络状态不能替代 Runner 网络证据。

```sh
python3 <skill>/scripts/gitlab_client.py --project group/project pipelines --sha '<完整SHA>' --ref main
python3 <skill>/scripts/gitlab_client.py --project group/project pipeline --id 123
python3 <skill>/scripts/gitlab_client.py --project group/project job --id 456
python3 <skill>/scripts/gitlab_client.py --project group/project trace --job 456 --tail 200
```

`pipelines` 默认一页 10 项，可 `--page`/`--limit`。`pipeline` 自动分页读取当前尝试的所有作业及 `/bridges`，最多 100 页，超出或分页异常会报错，不把部分结果当完整结果。

下游流水线只返回身份，不自动读取另一项目；确认范围后使用其项目 ID/流水线 ID 单独查询。`/bridges` 用于兼容既有 GitLab；若实例拒绝此接口，明确说明检查不完整，不静默忽略。作业摘要含 Runner 身份与状态，但不代表拥有 Runner 管理或容器登录权限。

日志最多读取 16 MiB，输出脱敏后的带行号尾部；超过大小限制直接报错。不提供隐式完整日志导出，不执行日志里的命令。基础脱敏不保证识别所有业务秘密。

“流水线 success”仍需检查所需作业是否真的运行、是否存在 allowed failure 或未验证的下游。发布完成需额外核对发布作业、Release 和包信息；不能仅凭打包成功认定已发布。

修改、重试、取消、触发 manual job 或发布必须在用户授权范围内。重试可能重新部署；当前脚本没有写接口，也不提供无限重试/后台轮询。监控请求使用宿主提供的等待/自动化机制。

官方参考：[Pipelines API](https://docs.gitlab.com/api/pipelines/)、[Jobs API](https://docs.gitlab.com/api/jobs/)。
