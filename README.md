# SmartRF Agent Skills

面向 SmartRF 开发与诊断场景的可安装 Agent Skills 仓库。Skill 遵循开放的
[Agent Skills 规范](https://agentskills.io/specification)，可供 Codex、Claude Code
以及其他兼容客户端使用。

## Skills

| Skill | 用途 | 目录 |
| --- | --- | --- |
| `analyze-smartrf-diagnostics` | 分析 SmartRF v4 全量诊断、链路状态、统计与 PHY 异常 | [`skills/analyze-smartrf-diagnostics`](skills/analyze-smartrf-diagnostics/) |
| `smartrf-debugio` | 使用 gx-dsview-cli 采集并分析无线 DebugIO 时序 | [`skills/smartrf-debugio`](skills/smartrf-debugio/) |

## 快速安装

安装前建议先预览 Skill 内容。`gh skill` 需要 GitHub CLI 2.90.0 或更高版本。

### 从 GitHub 安装

安装全部 Skill 到 Codex：

```bash
gh skill preview gerald0057/skills
gh skill install gerald0057/skills --all --agent codex --scope user
```

安装全部 Skill 到 Claude Code：

```bash
gh skill install gerald0057/skills --all --agent claude-code --scope user
```

只安装一个 Skill 时，将 `--all` 替换为 Skill 名称：

```bash
gh skill install gerald0057/skills analyze-smartrf-diagnostics --agent codex --scope user
```

### 从私有 GitLab 安装

先使用已有 SSH 凭据克隆仓库，再从本地目录安装。这样不要求 `gh` 直接访问 GitLab：

```bash
git clone ssh://git@218.75.120.100:9922/zhuhy0057/skills.git
cd skills
gh skill install . --from-local --all --agent codex --scope user
gh skill install . --from-local --all --agent claude-code --scope user
```

没有支持 `gh skill` 的 GitHub CLI 时，可以使用仓库自带脚本创建用户级符号链接：

```bash
./scripts/install-local.sh all
```

也可以只安装到一个客户端：

```bash
./scripts/install-local.sh codex
./scripts/install-local.sh claude-code
```

脚本不会覆盖已有文件或目录。符号链接安装适合私有仓库：后续 `git pull` 后 Skill
内容会立即更新。

## Claude Code 插件安装

仓库同时提供 Claude Code marketplace，可一次安装整个 Skill 集合。

GitHub：

```text
/plugin marketplace add gerald0057/skills
/plugin install smartrf-skills@gerald0057-skills
```

私有 GitLab：

```text
/plugin marketplace add ssh://git@218.75.120.100:9922/zhuhy0057/skills.git
/plugin install smartrf-skills@gerald0057-skills
```

私有仓库安装依赖本机已配置的 Git 凭据、`known_hosts` 和 `ssh-agent`。开发时也可在
仓库根目录执行 `claude --plugin-dir .` 直接加载。

## 更新

通过 `gh skill` 安装的 Skill：

```bash
gh skill update --all
```

通过私有 GitLab 本地安装时，先 `git pull`；符号链接安装无需再次复制，使用
`--from-local` 安装的用户重新执行对应安装命令即可。Claude Code marketplace 用户可运行
`/plugin marketplace update gerald0057-skills` 后再更新插件。

## 环境说明

- `analyze-smartrf-diagnostics` 是纯说明型 Skill；若工作区包含 SmartRF v4 源码，可进一步校准字段语义。
- `smartrf-debugio` 的实时采集能力依赖 Linux、`gx-dsview-cli` 和兼容逻辑分析仪；离线分析不要求连接硬件。
- Skill 可能指导 Agent 执行本地命令。安装前应检查 `SKILL.md` 和附带资源，并遵循客户端权限提示。

## 维护

新增、修改或发布 Skill 的流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。提交前运行：

```bash
python3 scripts/validate-repository.py
```

GitHub Actions 和 GitLab CI 都会执行同一验证脚本。

## License

[MIT](LICENSE)
