# SmartRF Agent Skills

面向 SmartRF 开发与诊断场景的可安装 Agent Skills 仓库。Skill 遵循开放的
[Agent Skills 规范](https://agentskills.io/specification)，可供 Codex、Claude Code
以及其他兼容客户端使用。

## Skills

| Skill | 用途 | 目录 |
| --- | --- | --- |
| `analyze-smartrf-diagnostics` | 分析 SmartRF v4 全量诊断、链路状态、统计与 PHY 异常 | [`skills/analyze-smartrf-diagnostics`](skills/analyze-smartrf-diagnostics/) |
| `smartrf-debugio` | 使用 gx-dsview-cli 采集并分析无线 DebugIO 时序 | [`skills/smartrf-debugio`](skills/smartrf-debugio/) |

## 安装

推荐使用 GitHub CLI 的 [`gh skill`](https://cli.github.com/manual/gh_skill_install)。它会发现
`skills/*/SKILL.md`，并把 Skill 安装到对应 Agent 的标准目录。

### 准备环境

#### 依赖一览

| 依赖 | 是否必需 | 用途 |
| --- | --- | --- |
| Git | 本地安装必需 | 克隆 GitHub、GitLab 仓库以及后续更新 |
| Python 3 | 推荐 | 运行仓库结构校验脚本；Skill 本身不依赖第三方 Python 包 |
| curl 或 wget | 安装工具时可能需要 | 下载 Codex、Claude Code 或 GitHub CLI 的官方安装资源 |
| OpenSSH 客户端 | 使用 SSH 克隆时必需 | 访问私有 GitLab 等 SSH Git 仓库 |
| POSIX shell | 符号链接安装必需 | 运行 `scripts/install-local.sh`；Linux、macOS 和 WSL 可直接使用 |
| GitHub CLI 2.90.0+ | 使用 `gh skill` 时必需 | 从 GitHub 安装，或从本地目录复制安装 Skill |
| Codex 或 Claude Code | 至少一个 | 实际加载和运行 Skill |
| `gx-dsview-cli` 与兼容逻辑分析仪 | 可选 | 仅 `smartrf-debugio` 实时采集需要；离线分析和安装不需要 |

本仓库不需要执行 `pip install`、`npm install` 或安装项目级 Python/Node.js 依赖。
Node.js 只在选择 npm 方式安装 Codex 或 Claude Code 时需要。

#### 1. 安装 Git 和 Python 3

Debian/Ubuntu：

```bash
sudo apt update
sudo apt install -y git python3 curl wget openssh-client
```

macOS：

```bash
xcode-select --install
```

`xcode-select` 会提供 Git。macOS 通常已有 Python 3；如果没有，可通过
[Homebrew](https://brew.sh/) 安装：

```bash
brew install python
```

Windows PowerShell：

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
```

Windows 原生环境建议使用后文的 `gh skill --from-local`。若要运行
`scripts/install-local.sh`，请使用 WSL；该脚本依赖 POSIX 符号链接工具。

验证基础工具：

```bash
git --version
python3 --version
```

Windows 上 Python 命令可能是 `python`，可用 `python scripts/validate-repository.py`
替代本文中的 `python3`。

#### 2. 安装 GitHub CLI

`gh skill` 从 GitHub CLI 2.90.0 开始提供。较旧版本即使能正常执行 `gh`，也没有
`gh skill` 子命令。

Debian/Ubuntu 应使用 [GitHub 官方软件源](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)，
不要依赖发行版自带的旧版本：

```bash
(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y)) \
  && sudo mkdir -p -m 755 /etc/apt/keyrings \
  && out=$(mktemp) \
  && wget -nv -O "$out" https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  && cat "$out" | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
  && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && sudo mkdir -p -m 755 /etc/apt/sources.list.d \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null \
  && sudo apt update \
  && sudo apt install gh -y
```

macOS 或已安装 Homebrew 的 Linux：

```bash
brew install gh
```

Windows PowerShell：

```powershell
winget install --id GitHub.cli -e
```

其他 Linux 发行版和离线二进制包见
[GitHub CLI 官方安装说明](https://github.com/cli/cli#installation)。安装后重新打开终端并验证：

```bash
gh --version
gh skill install --help
```

输出版本必须不低于 `2.90.0`，并且第二条命令必须显示 `gh skill install` 帮助。
旧版本可按安装方式升级：

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install gh

# Homebrew
brew upgrade gh
```

Windows PowerShell：

```powershell
winget upgrade --id GitHub.cli -e
```

登录 GitHub。安装公开仓库时也建议提前登录；访问私有 GitHub 仓库时必须使用有权限的账号：

```bash
gh auth login
gh auth status
```

#### 3. 安装目标 Agent

只需安装计划使用的 Agent，不要求同时安装 Codex 和 Claude Code。

安装 Codex CLI（macOS、Linux 或 WSL）：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
codex
```

首次运行 `codex` 时按提示登录。也可以使用 npm 安装：

```bash
npm install -g @openai/codex
```

更多平台和认证方式见 [Codex CLI 官方文档](https://learn.chatgpt.com/docs/codex/cli)。

安装 Claude Code（macOS、Linux 或 WSL）：

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
claude
```

Windows PowerShell：

```powershell
& ([scriptblock]::Create((irm https://claude.ai/install.ps1))) stable
claude --version
```

也可以使用 npm 安装；当前 npm 包要求 Node.js 22 或更高版本：

```bash
npm install -g @anthropic-ai/claude-code
```

更多安装方式见 [Claude Code 官方文档](https://code.claude.com/docs/en/getting-started)。

#### 4. 最终环境检查

从 GitHub 远程安装前，最少需要确认 `gh skill` 和目标 Agent：

```bash
gh --version
gh skill install --help
# 根据目标选择其中一项或两项
codex --version
claude --version
```

从本地仓库安装时，再检查：

```bash
git --version
python3 --version
```

从私有 GitLab 克隆时，还需要提前配置 Git HTTPS 凭据或 SSH key，并确保 `ssh -V` 可用；
这种本地安装路径不要求 `gh` 登录 GitLab。

`--scope user` 表示对当前用户的所有项目生效；将其改为 `--scope project`，则只在当前
Git 仓库内安装，适合测试或团队项目。

### 方法一：从 GitHub 安装（推荐）

安装前可以先预览仓库中的 Skill：

```bash
gh skill preview gerald0057/skills analyze-smartrf-diagnostics
gh skill preview gerald0057/skills smartrf-debugio
```

安装全部 Skill 到 Codex：

```bash
gh skill install gerald0057/skills \
  --all \
  --agent codex \
  --scope user
```

安装全部 Skill 到 Claude Code：

```bash
gh skill install gerald0057/skills \
  --all \
  --agent claude-code \
  --scope user
```

只安装一个 Skill：

```bash
gh skill install gerald0057/skills \
  analyze-smartrf-diagnostics \
  --agent codex \
  --scope user
```

仓库发布 tag 后，可以安装指定 tag 或 commit SHA。例如固定到 `v0.1.0`：

```bash
gh skill install gerald0057/skills \
  analyze-smartrf-diagnostics@v0.1.0 \
  --agent codex \
  --scope user
```

### 方法二：从本地仓库安装

本地安装适用于以下情况：

- 使用私有 GitLab 或其他非 GitHub Git 服务；
- 需要验证尚未推送的修改；
- 开发期间希望 Skill 跟随本地仓库更新。

先克隆仓库。任选一个来源：

```bash
# GitHub
git clone https://github.com/gerald0057/skills.git smartrf-skills

# 私有 GitLab（需要已配置 SSH key）
git clone \
  ssh://git@218.75.120.100:9922/zhuhy0057/skills.git \
  smartrf-skills
```

进入仓库并验证文件结构：

```bash
cd smartrf-skills
python3 scripts/validate-repository.py
```

预期输出：

```text
Repository validation passed.
```

#### 使用 `gh skill --from-local` 复制安装

安装到 Codex：

```bash
gh skill install . \
  --from-local \
  --all \
  --agent codex \
  --scope user
```

安装到 Claude Code：

```bash
gh skill install . \
  --from-local \
  --all \
  --agent claude-code \
  --scope user
```

这种方式会把 Skill 复制到 Agent 的用户目录。仓库更新后，需要重新执行安装命令。

#### 使用仓库脚本创建符号链接

没有支持 `gh skill` 的 GitHub CLI，或希望 `git pull` 后立即使用新内容时，可以运行：

```bash
./scripts/install-local.sh all
```

只安装到一个 Agent：

```bash
./scripts/install-local.sh codex
./scripts/install-local.sh claude-code
```

脚本创建用户级符号链接，不复制文件，也不会覆盖已有文件或目录。重复执行同一个安装
命令是安全的，已存在且指向正确位置的链接会显示 `already installed`。

### 安装后验证

查看 `gh skill` 管理的安装记录：

```bash
gh skill list --agent codex --scope user
gh skill list --agent claude-code --scope user
```

查看完整来源和安装路径：

```bash
gh skill list \
  --scope user \
  --json skillName,agentHosts,sourceURL,path,version
```

在 Codex 中运行 `/skills`，然后显式调用：

```text
$analyze-smartrf-diagnostics 请说明分析 srf_debug -a 的检查顺序，不要执行命令。
```

在 Claude Code 中直接调用：

```text
/analyze-smartrf-diagnostics 请说明诊断流程，不要执行命令。
```

如果新安装的 Skill 没有出现，重新启动对应 Agent。

## Claude Code 插件安装（可选）

除了 standalone skill，仓库还提供 Claude Code marketplace，可以一次安装整个
`smartrf-skills` 插件。

从 GitHub 安装：

```bash
claude plugin marketplace add gerald0057/skills
claude plugin install smartrf-skills@gerald0057-skills
```

从私有 GitLab 安装：

```bash
claude plugin marketplace add \
  ssh://git@218.75.120.100:9922/zhuhy0057/skills.git
claude plugin install smartrf-skills@gerald0057-skills
```

验证插件和其中的 Skill：

```bash
claude plugin list
claude plugin details smartrf-skills@gerald0057-skills
```

插件 Skill 带有命名空间，例如：

```text
/smartrf-skills:analyze-smartrf-diagnostics 请说明诊断流程。
```

开发时也可以在仓库根目录直接加载，不创建安装记录：

```bash
claude --plugin-dir .
```

## 更新、重复安装与卸载

### GitHub 安装

检查并更新所有由 `gh skill` 管理的 Skill：

```bash
gh skill update --all
```

需要覆盖本地已有版本时，可以重复安装并使用 `--force`：

```bash
gh skill install gerald0057/skills \
  --all \
  --agent codex \
  --scope user \
  --force
```

### 本地复制安装

```bash
cd /path/to/smartrf-skills
git pull
gh skill install . \
  --from-local \
  --all \
  --agent codex \
  --scope user \
  --force
```

Claude Code 用户将 `--agent codex` 替换为 `--agent claude-code`。

### 本地符号链接安装

符号链接会直接读取仓库内容，只需更新仓库：

```bash
cd /path/to/smartrf-skills
git pull
```

卸载符号链接：

```bash
unlink "$HOME/.agents/skills/analyze-smartrf-diagnostics"
unlink "$HOME/.agents/skills/smartrf-debugio"
unlink "$HOME/.claude/skills/analyze-smartrf-diagnostics"
unlink "$HOME/.claude/skills/smartrf-debugio"
```

### 卸载 standalone skill

当前 `gh skill` 没有单独的卸载子命令。先用 `gh skill list` 确认 `sourceURL` 和精确安装
路径，再删除对应目录：

```bash
gh skill list \
  --scope user \
  --json skillName,agentHosts,sourceURL,path
```

Codex 用户级默认路径为 `~/.agents/skills/<skill-name>`；Claude Code 用户级默认路径为
`~/.claude/skills/<skill-name>`。不要删除来源不明的同名目录。

### 更新或卸载 Claude Code 插件

```bash
claude plugin marketplace update gerald0057-skills
claude plugin update smartrf-skills@gerald0057-skills
```

仅卸载插件、保留 marketplace：

```bash
claude plugin uninstall smartrf-skills@gerald0057-skills
```

删除 marketplace，并同时卸载从中安装的插件：

```bash
claude plugin marketplace remove gerald0057-skills
```

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
