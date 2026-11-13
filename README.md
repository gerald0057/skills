# Agent Skills

集中维护、可分别安装的 Agent Skills 仓库。Skill 遵循开放的
[Agent Skills 规范](https://agentskills.io/specification)，可供 Codex、Claude Code、OpenCode
以及其他兼容客户端使用。

## Skills

| Skill | 用途 | 目录 |
| --- | --- | --- |
| `diagram-design` | 使用中文系统字体生成自包含 HTML/SVG 图示；支持本地导入与离线自检 | [`skills/diagram-design`](skills/diagram-design/) |
| `fwscope` | 校验 ncm2 artifact bundle，并分析固件 RAM/Flash 占用与 RAM/XIP/ROM 执行路径 | [`skills/fwscope`](skills/fwscope/) |
| `fwsize-report` | 构建并核验固件，分层统计 Flash/RAM，生成 Markdown 明细和黑白 PDF 报告 | [`skills/fwsize-report`](skills/fwsize-report/) |
| `gx-workbench-plugin` | 按宿主实际 API、服务和编译 SDK 开发通用插件，定位并修复版本不兼容 | [`skills/gx-workbench-plugin`](skills/gx-workbench-plugin/) |
| `logic-analyzer` | 安全采集并流式检查、测量 DSView 数字逻辑波形 | [`skills/logic-analyzer`](skills/logic-analyzer/) |
| `smartrf-diagnostics` | 分析 SmartRF v4 全量诊断、链路状态、统计与 PHY 异常 | [`skills/smartrf-diagnostics`](skills/smartrf-diagnostics/) |
| `smartrf-debugio` | 使用 gx-dsview-cli 采集并分析无线 DebugIO 时序 | [`skills/smartrf-debugio`](skills/smartrf-debugio/) |
| `redmine-access` | 提供紧凑 Redmine 查询、经确认的附件下载与本地分析，以及同一 Issue 操作包的安全写入 | [`skills/redmine-access`](skills/redmine-access/) |
| `gx-gitlab` | 只读查询 GitLab 实例、项目、提交、Issue/MR、发布及包信息，并诊断 CI 状态与日志 | [`skills/gx-gitlab`](skills/gx-gitlab/) |
| `weekly-report` | 根据 iBrain 项目看板整理本周总结并合并下周计划 | [`skills/weekly-report`](skills/weekly-report/) |
| `git-commit-guide` | 根据实际变更生成或检查规范的中文 Git 提交信息 | [`skills/git-commit-guide`](skills/git-commit-guide/) |

`redmine-access` 首次使用时通过本地交互式向导配置，API Key 保存在
`~/.config/skills/redmine-access/`，不会写入本仓库。读取默认返回有限分页的摘要；附件下载和所有写入均需确认，同一 Issue 可将明确列出的多个附件与一次说明更新合为一个操作包，删除永久禁止。

`gx-gitlab` 使用 Python 3.10+ 标准库（Linux/macOS/WSL），配置和 Token 保存在
`~/.config/skills/gx-gitlab/`，不进入仓库。只提供 GET 查询，不触发 CI、合并 MR 或发布。
连接配置只保存实例，不保存默认项目；安装不创建连接配置、不访问 GitLab。`status` 和默认 `doctor` 不查询项目，项目操作每次显式传入 `--project`，旧配置中的项目字段会被忽略。
首次配置、隐藏登录、Token 更新、多项目/多实例和旧配置迁移见 [访问说明](skills/gx-gitlab/references/access.md)；
日常命令、输出格式与隐私边界见 [查询说明](skills/gx-gitlab/references/queries.md)，
流水线核对与日志定位见 [CI 说明](skills/gx-gitlab/references/ci.md)。

## 仓库与发布边界

- `skills/<skill-name>/` 是独立能力和独立安装单元；每个目录自己维护触发说明、执行规则与资源。
- 根 README 是动态目录，新增 Skill 时在这里增加索引。
- 根 `.codex-plugin/`、`.claude-plugin/` 是可选的整包安装元数据，只描述集合本身，不枚举具体 Skill 或场景。
- 新增 Skill 不需要改写根插件说明；仅在发布新的整包版本时同步提升两个插件 manifest 的版本。
- 若将来要求每个 Skill 都能在插件市场中单独安装，应为它建立独立插件包和 marketplace 条目，而不是继续扩充根 manifest。

## 安装

推荐使用 GitHub CLI 的 [`gh skill`](https://cli.github.com/manual/gh_skill_install)。它会发现
`skills/*/SKILL.md`，并把 Skill 安装到对应 Agent 的标准目录。

### 准备环境

#### 依赖一览

| 依赖 | 是否必需 | 用途 |
| --- | --- | --- |
| Git | 本地安装必需 | 克隆 GitHub、GitLab 仓库以及后续更新 |
| Python 3（`gx-gitlab` 要求 3.10+） | 使用连接类 Skill 或维护仓库时必需 | 运行受控 Redmine/GitLab 客户端和仓库结构校验；不依赖第三方 Python 包 |
| curl 或 wget | 安装工具时可能需要 | 下载 Codex、Claude Code 或 GitHub CLI 的官方安装资源 |
| OpenSSH 客户端 | 使用 SSH 克隆时必需 | 访问私有 GitLab 等 SSH Git 仓库 |
| POSIX shell | 符号链接安装必需 | 运行 `scripts/install-local.sh`；Linux、macOS 和 WSL 可直接使用 |
| GitHub CLI 2.90.0+ | 使用 `gh skill` 时必需 | 从 GitHub 安装，或从本地目录复制安装 Skill |
| Codex、Claude Code、OpenCode 或 DSH | 至少一个 | 实际加载和运行 Skill；OpenCode 按 V2 说明 |
| `gx-dsview-cli` 与兼容逻辑分析仪 | 可选 | 仅 `smartrf-debugio` 实时采集需要；离线分析和安装不需要 |

仓库安装、结构校验和核心脚本测试不需要项目级 Python/Node.js 依赖。
`fwsize-report` 的统计核心使用 Python 3.11+ 标准库；PDF 生成另需 WeasyPrint、
中文宋体风格字体，PDF 验证另需 Poppler。运行时依赖见对应 Skill，安装 Skill 不自动安装这些工具。
Node.js 只在选择 npm 方式安装 Codex 或 Claude Code 时需要。

#### 自动检查本地环境

在仓库根目录运行 Doctor，可以一次检查 Git、GitHub CLI、`gh skill`、Python 3、SSH
以及 Codex、Claude Code、OpenCode、DSH，并检查 `gh skill` 是否支持 OpenCode 目标：

```bash
./scripts/doctor.sh
```

成功输出示例（版本和安装路径仅作示意）：

```text
Local environment doctor

[PASS] git: git version 2.34.1 (/usr/bin/git)
[PASS] gh: 2.102.0 (/usr/bin/gh)
[PASS] gh skill: install command is available
[PASS] gh skill: opencode agent is available
[PASS] python: Python 3.13.13 (/opt/python/bin/python3)
[PASS] ssh: OpenSSH_8.9p1, OpenSSL 3.0.2 (/usr/bin/ssh)
[PASS] codex: codex-cli 0.146.0 (/home/user/.local/bin/codex)
[PASS] claude: 2.1.222 (Claude Code) (/home/user/.local/bin/claude)
[PASS] opencode: opencode v2.2.7 (/home/user/.local/bin/opencode)
[PASS] dsh: 0.2.2-rc.1 (/home/user/.local/share/deepseek-harness/bin/dsh)

Summary: 10 passed, 0 warnings, 0 failures.
```

Doctor 只读取本地命令和版本信息，不联网、不修改配置，也不输出凭据。Git、GitHub CLI
2.90.0+、`gh skill` 和 Python 3 缺失时显示 `FAIL` 并返回退出码 `1`；SSH 或 Agent
未安装时仅显示 `WARN`。传入未知参数时返回退出码 `2`。
`gh skill` 可用但不支持 OpenCode 目标时也仅告警，可升级 GitHub CLI 或改用符号链接安装。
不使用 GitHub CLI 的本地符号链接安装无需满足 Doctor 中的 `gh` 检查。

该脚本需要 POSIX shell；Linux、macOS 和 WSL 可直接运行。Windows 原生 PowerShell
用户可按下面各节的验证命令逐项检查。

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
OpenCode 用户还需确认帮助中的支持列表包含 `OpenCode (opencode)`；`2.90.0` 只是
`gh skill` 的基础下限，不保证支持每个 Agent。本仓库已核实 GitHub CLI `2.102.0`
提供该目标；不支持时请升级，或使用后文的符号链接安装。
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

只需安装计划使用的 Agent，不要求同时安装三个客户端。

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

安装 OpenCode：按 [OpenCode 官方指南](https://opencode.ai/docs/) 选择适合本机的安装方式，
然后验证并启动：

```bash
opencode --version
opencode
```

首次运行时按客户端提示配置模型服务。本仓库不配置模型、密钥或 MCP；以下 OpenCode
操作按 V2 编写，本地路径行为已在 `v2.2.7` 核实。

#### 4. 最终环境检查

从 GitHub 远程安装前，最少需要确认 `gh skill` 和目标 Agent：

```bash
gh --version
gh skill install --help
# 根据目标选择需要的客户端
codex --version
claude --version
opencode --version
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
gh skill preview gerald0057/skills smartrf-diagnostics
gh skill preview gerald0057/skills smartrf-debugio
gh skill preview gerald0057/skills redmine-access
gh skill preview gerald0057/skills gx-gitlab
gh skill preview gerald0057/skills weekly-report
gh skill preview gerald0057/skills fwscope
gh skill preview gerald0057/skills logic-analyzer
gh skill preview gerald0057/skills git-commit-guide
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

安装全部 Skill 到 OpenCode：

```bash
gh skill install gerald0057/skills \
  --all \
  --agent opencode \
  --scope user
```

只安装一个 Skill：

```bash
gh skill install gerald0057/skills \
  smartrf-diagnostics \
  --agent codex \
  --scope user
```

仓库发布 tag 后，可以安装指定 tag 或 commit SHA。例如固定到 `v0.4.0`：

```bash
gh skill install gerald0057/skills \
  redmine-access@v0.4.0 \
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
git clone https://github.com/gerald0057/skills.git skills

# 私有 GitLab（需要已配置 SSH key）
git clone \
  ssh://git@218.75.120.100:9922/zhuhy0057/skills.git \
  skills
```

进入仓库并验证文件结构：

```bash
cd skills
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

安装到 OpenCode：

```bash
gh skill install . \
  --from-local \
  --all \
  --agent opencode \
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
./scripts/install-local.sh dsh
./scripts/install-local.sh opencode
```

只安装一个 Skill，可在 Agent 参数后追加 Skill 名称：

```bash
./scripts/install-local.sh codex redmine-access
./scripts/install-local.sh opencode redmine-access
./scripts/install-local.sh all redmine-access
```

脚本创建用户级符号链接，不复制文件，也不会覆盖已有文件或目录。重复执行同一个安装
命令是安全的，已存在且指向正确位置的链接会显示 `already installed`。

`all`（也是不传参数时的默认值）会为 Codex、Claude Code、DSH、OpenCode 四个客户端创建链接，
不要求安装四个客户端。DSH 原生目标为 `${DSH_HOME:-~/.dsh}/skills`；若设置了非空
`DSH_HOME`，则使用 `$DSH_HOME/skills`，该变量应为绝对路径。DSH 也会读取
`~/.agents/skills`（Codex 目标）以及项目级 `.dsh/skills`、`.agents/skills`；
同名时 `~/.dsh/skills` 优先级高于 `~/.agents/skills`。OpenCode 原生目标为
`~/.config/opencode/skills/`；若设置了非空 `XDG_CONFIG_HOME`，则使用
`$XDG_CONFIG_HOME/opencode/skills/`，该变量应为绝对路径。
脚本只创建 Skill 链接，不修改 `opencode.json(c)`、连接配置或凭据，也不安装运行依赖。

### 安装后验证

查看 `gh skill` 管理的安装记录：

```bash
gh skill list --agent codex --scope user
gh skill list --agent claude-code --scope user
gh skill list --agent opencode --scope user
```

查看完整来源和安装路径：

```bash
gh skill list \
  --scope user \
  --json skillName,agentHosts,sourceURL,path,version
```

在 Codex 中运行 `/skills`，然后显式调用：

```text
$smartrf-diagnostics 请说明分析 srf_debug -a 的检查顺序，不要执行命令。
```

在 Claude Code 中直接调用：

```text
/smartrf-diagnostics 请说明诊断流程，不要执行命令。
```

在 OpenCode 中显式调用（`@`，不是 Codex 的 `$` 或 Claude Code 的 `/`）：

```text
@git-commit-guide 请说明提交信息规则，不要执行命令。
@gx-gitlab 请只读取 references/queries.md 并说明项目查询参数，不要运行脚本或联网。
```

第一条验证 Skill 加载，第二条验证随 Skill 安装的相对路径资源可读；这些只读检查不代表
远端服务或硬件已通过验收。Redmine 写入、附件下载、硬件采集等仍遵守各 Skill 的确认规则。

在 DSH 中可用 `/skill-name` 显式调用，或让模型按目录描述自动加载：

```text
/smartrf-diagnostics 请说明诊断流程，不要执行命令。
```

DSH 的技能来自 `${DSH_HOME:-~/.dsh}/skills`（`install-local.sh dsh` 的安装目标），
也会读取 `~/.agents/skills` 等兼容目录；两者同名时 `~/.dsh/skills` 优先。

Codex、Claude Code 中新安装的 Skill 没有出现时，重新启动对应 Agent。
DSH 的 skill 目录带 watcher，通常无需重启，新会话即可确认目录刷新。
OpenCode 配置会自动重载；未刷新时先执行 `/reload`，无需为安装 Skill 重启后台服务。

#### OpenCode 同名来源排查

OpenCode 还兼容读取用户级 `~/.agents/skills/`、`~/.claude/skills/` 和项目级
`.agents/skills/`、`.claude/skills/`；通过这些目录安装的 Skill 可能已经可用，无需重复安装。
项目也可以使用原生 `.opencode/skills/`，因此 `gh skill --scope project` 使用兼容目录
并不表示安装失败。`gh skill list` 只列出其自身管理的安装，不包含本脚本创建的链接。

同名 ID 只加载一个定义。V2 优先级由低到高为：内置、Claude 兼容来源、Agents 兼容来源、
OpenCode 原生用户目录、原生项目目录、显式 `skills` 配置来源。安装后内容不符时检查
这些位置及 `opencode.json(c)` 的 `skills` 配置；不要覆盖或删除来源不明的同名 Skill。
加载被拒绝时还需检查当前 Agent 的 `skill` 权限。详见 [OpenCode Skills 文档](https://opencode.ai/docs/skills)。

## Claude Code 插件安装（可选）

除了 standalone skill，仓库还提供 Claude Code marketplace，可以一次安装整个
`skills` 插件。

从 GitHub 安装：

```bash
claude plugin marketplace add gerald0057/skills
claude plugin install skills@gerald0057-skills
```

从私有 GitLab 安装：

```bash
claude plugin marketplace add \
  ssh://git@218.75.120.100:9922/zhuhy0057/skills.git
claude plugin install skills@gerald0057-skills
```

验证插件和其中的 Skill：

```bash
claude plugin list
claude plugin details skills@gerald0057-skills
```

插件 Skill 带有命名空间，例如：

```text
/skills:smartrf-diagnostics 请说明诊断流程。
```

开发时也可以在仓库根目录直接加载，不创建安装记录：

```bash
claude --plugin-dir .
```

## 更新、重复安装与卸载

### 从 `smartrf-skills` 迁移到 `skills`

从 `0.4.0` 开始，整包插件机器名由 `smartrf-skills` 改为 `skills`。各个 Skill
名称和 standalone 安装路径没有变化；只有通过 Codex 或 Claude Code marketplace
安装整包插件的用户需要迁移。

先更新本仓库或 marketplace，然后在仓库根目录预览迁移动作：

```bash
python3 scripts/migrate-plugin-name.py --agent all
```

确认输出后执行：

```bash
python3 scripts/migrate-plugin-name.py --agent all --apply
```

也可以只迁移一个客户端：

```bash
python3 scripts/migrate-plugin-name.py --agent codex --apply
python3 scripts/migrate-plugin-name.py \
  --agent claude-code \
  --claude-scope user \
  --apply
```

脚本只迁移已经安装的旧整包插件：先刷新 `gerald0057-skills` marketplace，再安装或更新
并确认 `skills@gerald0057-skills`，然后才卸载旧插件。即使新旧插件同时存在，也会先更新
新插件；安装、更新或验证失败时保留旧插件。只有新插件时脚本保持无操作，后续新增 Skill
按下文“更新整包插件”操作。迁移后重新启动客户端或新建任务，使新命名空间生效。

### 从 0.1.x 迁移到 0.2.0

`analyze-smartrf-diagnostics` 已重命名为 `smartrf-diagnostics`。更新工具不会自动把已经
安装的旧目录改名，因此升级时应先按下文重新安装全部 Skill，确认新名称可用，再按照
“卸载 standalone skill”中的方法删除旧名称对应的精确目录。符号链接安装用户可以先执行：

```bash
unlink "$HOME/.agents/skills/analyze-smartrf-diagnostics"
unlink "$HOME/.claude/skills/analyze-smartrf-diagnostics"
./scripts/install-local.sh all
```

只删除实际存在且确认属于本仓库的旧链接；如果只安装了一个 Agent，只执行对应的
`unlink` 和安装命令。

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
cd /path/to/skills
git pull
gh skill install . \
  --from-local \
  --all \
  --agent codex \
  --scope user \
  --force
```

Claude Code、OpenCode 用户分别将 `--agent codex` 替换为 `--agent claude-code`、`--agent opencode`。
重新安装前先核对目标路径，尤其不要用 `--force` 覆盖本地符号链接或来源不明的安装。

### 本地符号链接安装

已有符号链接会直接读取仓库中的更新；新增 Skill 仍需在拉取后重新运行安装脚本，以创建
对应的新链接：

```bash
cd /path/to/skills
git pull
./scripts/install-local.sh all
```

卸载符号链接：

```bash
unlink "$HOME/.agents/skills/smartrf-diagnostics"
unlink "$HOME/.agents/skills/smartrf-debugio"
unlink "$HOME/.claude/skills/smartrf-diagnostics"
unlink "$HOME/.claude/skills/smartrf-debugio"
# OpenCode：仅对已确认属于本仓库的对应链接执行
unlink "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills/smartrf-diagnostics"
unlink "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills/smartrf-debugio"
```

只执行实际安装的客户端对应命令。OpenCode 可能继续发现兼容目录中的同名 Skill；
卸载原生链接不等于卸载其他客户端的共享来源。不要为卸载一个客户端而删除仍在使用的共享目录。

### 卸载 standalone skill

当前 `gh skill` 没有单独的卸载子命令。先用 `gh skill list` 确认 `sourceURL` 和精确安装
路径，再删除对应目录：

```bash
gh skill list \
  --scope user \
  --json skillName,agentHosts,sourceURL,path
```

Codex 用户级默认路径为 `~/.agents/skills/<skill-name>`；Claude Code 用户级默认路径为
`~/.claude/skills/<skill-name>`。OpenCode 原生用户级默认路径为
`~/.config/opencode/skills/<skill-name>`（自定义配置目录见前文）；`gh skill` 的实际路径以
`gh skill list` 为准。不要删除来源不明的同名目录。

### 更新整包插件

Codex：

```bash
codex plugin marketplace upgrade gerald0057-skills
codex plugin add skills@gerald0057-skills
```

Claude Code：

```bash
claude plugin marketplace update gerald0057-skills
claude plugin update skills@gerald0057-skills
```

更新后重新启动客户端或新建任务，才能加载新增或修改后的 Skill。

### 卸载 Claude Code 插件

仅卸载插件、保留 marketplace：

```bash
claude plugin uninstall skills@gerald0057-skills
```

删除 marketplace，并同时卸载从中安装的插件：

```bash
claude plugin marketplace remove gerald0057-skills
```

## 环境说明

- `smartrf-diagnostics` 是纯说明型 Skill；若工作区包含 SmartRF v4 源码，可进一步校准字段语义。
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

## Diagram Design 来源与安装

该技能引入 https://github.com/cathrynlavery/diagram-design 的 `skills/diagram-design/`，固定到 `11b08eade8049c441ed3df1895e3b527c26c0fbe`，导入日期 2026-10-14。完整来源与局部适配见 [来源记录](skills/diagram-design/references/upstream-provenance.md)。本技能保留 [上游 MIT](skills/diagram-design/LICENSE) 及 [第三方声明](skills/diagram-design/THIRD_PARTY_LICENSES.md)，许可证仅适用于其对应内容。

窄范围安装：`./scripts/install-local.sh codex diagram-design`。安装后在 Codex Desktop 的新会话查看技能并使用 `$diagram-design 请用中文系统字体绘制一个自包含 HTML 架构图，保留默认配色，不生成 PNG。`。文件和链接检查不代表 Desktop 已实际调用。

默认使用 HTML/SVG，五个 Python 工具仅依赖标准库；不要求 PNG、Playwright 或 Chromium。上游插件命令及仓库开发校验器不随独立技能安装。回滚先移除用户技能目录中本技能对应的符号链接，再删除新增技能目录、`tests/test_diagram_design.py` 并撤销本次 README 两处新增内容；不得覆盖其他变更。
