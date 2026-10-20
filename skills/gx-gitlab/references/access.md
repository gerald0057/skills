# 连接配置与权限

## 最少需要的信息

| 信息 | 用途 |
| --- | --- |
| 完整 GitLab 根 URL（协议、主机、端口，若有则含部署子路径） | 构造同一实例的 `/api/v4` 地址 |
| 项目路径 `group/project` 或数字 ID | 仅项目级查询时显式提供，不写入连接配置 |
| 本地保存的访问 Token | 查询私有项目、流水线和日志 |
| 对象标识（如分支、完整 SHA、Issue/MR IID、流水线 ID） | 按请求精确限定对象 |
| 内网/VPN/代理条件（如有） | 区分网络不可达与认证失败 |

无需 Runner 管理权限或实际作业容器的 shell。安装 skill 不创建连接配置、不访问任何 GitLab；每位开发者使用自己的实例和凭据，不需要访问某个固定仓库。只在用户要求配置或迁移时写入本机配置。

## 创建只读凭据

优先在项目的 **Settings → Access tokens** 创建独立 Token：

- 名称如 `gx-gitlab-read`，设置明确的短期到期时间。
- Scope 选择 `read_api`；不要只选 `read_repository`，它不是通用 CI API 权限。
- 角色从 Reporter 开始，实际访问受实例版本、项目可见性和权限配置影响。遇到拒绝先确认项目与权限，不自动升级。
- 项目 Token 的创建通常需要 Maintainer/Owner。没有入口时可用个人设置中的 Personal access token，仍选择 `read_api`；个人 Token 的可见范围随用户权限更大，应注意隔离。

官方依据：[Token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/)、[项目 Token](https://docs.gitlab.com/user/project/settings/project_access_tokens/)、[API 认证](https://docs.gitlab.com/api/rest/authentication/)。

在用户本机终端运行 `login` 隐藏输入；不要将 Token 放进聊天、命令参数、URL、Git remote、仓库或 skill。脚本以当前用户专用的 `0600` 文件保存，目录使用 `0700`；Token 不加密，仍需保护本机账户。若泄露，在 GitLab 撤销/轮换；本地删除文件不等于撤销远端 Token。

## 配置与验证

下文 `<skill>` 表示 `SKILL.md` 所在目录。通过本仓库 `install-local.sh codex gx-gitlab` 安装时，可先在自己的终端设置脚本路径；插件安装或其他安装方式需改成实际路径。要求 Python 3.10+，不需要 `sudo` 或额外的 pip 包。

```sh
gx_gitlab_client="$HOME/.agents/skills/gx-gitlab/scripts/gitlab_client.py"
python3 "$gx_gitlab_client" --help
```

### 首次配置

将以下通用域名替换为自己的实例地址，根 URL 不含 `/api/v4`。首次配置只保存实例，不选择项目；HTTPS 与 HTTP 两种配置命令二选一，不要对同一配置重复执行。

```sh
python3 "$gx_gitlab_client" configure \
  --url 'https://gitlab.example.com'
```

只有用户明确选择 HTTP 部署时，改用以下命令。HTTP 会明文传输 Token，内网也不能消除此风险：

```sh
python3 "$gx_gitlab_client" configure \
  --url 'http://gitlab.example.com' --allow-http
```

`configure` 不接受 `--project`。随后由用户在本地终端运行：

```sh
python3 "$gx_gitlab_client" login
python3 "$gx_gitlab_client" status
python3 "$gx_gitlab_client" doctor
```

`login` 会隐藏输入，只在本地保存，不代表认证成功。`status` 返回 `authenticated: true` 才表示身份验证通过；核对返回的身份和实例地址。版本接口不可用不等于身份验证失败。`status` 和默认 `doctor` 都不访问项目，不是管理员健康检查，也不证明任何 CI 成功。

### 指定本次项目

只有用户要求查询某个项目时，才显式传入项目路径或数字 ID，项目路径不含 `.git`：

```sh
python3 "$gx_gitlab_client" --project 'group/project' project
python3 "$gx_gitlab_client" --project 'group/project' doctor
python3 "$gx_gitlab_client" --project 'group/project' pipelines
```

项目详情返回后核对 `id`、`path_with_namespace` 和克隆链接；带项目的 `doctor` 才额外检查该项目的流水线读取权限。脚本不从配置、环境变量、Git remote 或历史查询补全项目。缺少项目的项目级命令会在读取凭据、联网前报错。

`configure`、`login`、`status`、`projects` 不接受 `--project`，误传会报错；全局 `--project` 必须放在支持它的子命令之前。

### 已有配置与 Token 更新

已有 `config.json` 时跳过 `configure`，直接 `login`（未保存 Token）或 `status`（已有 Token）。`configure` 不覆盖已有配置，重复执行的“File already exists”不是需要删除凭据的错误。

同一实例的 Token 到期或轮换后，在本地终端显式替换，再验证：

```sh
python3 "$gx_gitlab_client" login --replace
python3 "$gx_gitlab_client" status
```

此操作只更新本地文件，不撤销旧 Token；远端撤销/轮换需要在 GitLab 中完成。

### 保存位置与配置格式

| 文件 | 内容与权限 |
| --- | --- |
| `~/.config/skills/gx-gitlab/config.json` | 非秘密连接配置，`0600`；父目录 `0700` |
| 同目录 `config.token` | 明文 Token，`0600`；不得输出、提交或放入聊天 |

`configure` 自动生成配置，不需要手工创建文件；以下仅为字段说明，不含凭据：

```json
{
  "url": "https://gitlab.example.com",
  "allow_http": false
}
```

新配置只保存这两个键。读取旧配置时允许存在 `project`，但会忽略并提示，不作为查询目标。不要把项目或 Token 填入新 JSON；Token 也不能通过命令行参数传入。更换实例使用独立配置，不修改主机后复用旧 Token。

### 多项目与多实例

每次查询独立指定项目，不修改连接配置，也不保存“上一次项目”；项目 Token 通常不能访问另一个私有项目：

```sh
python3 "$gx_gitlab_client" --project 'other/project' project
```

另一个实例使用独立配置，并在每次调用时指定相同的 `--config`：

```sh
gx_gitlab_profile="$HOME/.config/skills/gx-gitlab/secondary/config.json"
python3 "$gx_gitlab_client" --config "$gx_gitlab_profile" configure \
  --url 'https://gitlab-secondary.example.com'
python3 "$gx_gitlab_client" --config "$gx_gitlab_profile" login
python3 "$gx_gitlab_client" --config "$gx_gitlab_profile" status
python3 "$gx_gitlab_client" --config "$gx_gitlab_profile" --project 'group/project' project
```

全局 `--config` 和查询用的 `--project` 放在子命令之前。配置和登录均不联网；`status`/`doctor` 等查询命令才执行只读 API 请求。

## 旧配置与 gitlab-ci 迁移

旧配置中的 `project` 字段不再生效：读取时忽略该字段并在 stderr 提示，不输出字段值，也不自动修改配置。即使它指向无权访问的项目，默认 `status`/`doctor` 也不会查询它。

从 `gitlab-ci` 使用旧路径时必须明确选择，不自动探测旧配置或 Token：

```sh
python3 <skill>/scripts/gitlab_client.py --config ~/.config/gitlab-ci/config.json status
```

经用户要求迁移时，先备份配置并保持 `0600`，再仅保留 `url`、`allow_http`，不得把旧项目复制为新默认值。原目录的 Token 不需要读取或改动；若确需更换配置路径，只有获得授权后才另外迁移相邻 Token，目录/文件保持 `0700/0600`，不经过命令参数或工具输出、不覆盖已有目标。保留原文件用于恢复，普通查询不执行这些写操作。

不把配置和 Token 放进 skill 源码。更新 skill 或移除本地字段不撤销远端 Token。

## 访问诊断

| 观察 | 解释与下一步 |
| --- | --- |
| `requires --project` | 为本次项目查询显式指定项目；不要修改连接配置来补全 |
| `does not accept --project` | 该命令是实例/本地操作；项目验证请用 `--project group/project doctor` |
| `legacy project field ignored` | 旧字段已失效，普通查询未修改文件；确认迁移后备份并移除该字段 |
| 无法打开 `config.json` / `config.token` | 先配置 / 在本地终端运行 `login`；使用自定义配置时核对 `--config` 路径 |
| 文件权限被拒绝 | 确认文件归当前用户所有、普通文件 `0600`、目录 `0700`；不使用 `sudo`、符号链接或输出 Token 排错 |
| `login` 要求交互终端 | 在自己的终端运行，不通过管道、聊天或 Agent 代填 Token |
| DNS、连接超时 | 核对域名、VPN、代理；沙箱限制需要授权的同目标只读重试，不扫描端口 |
| 首页 302 到登录页 | 只证明网页入口可达，不证明已认证 |
| 401 | Token 无效、过期、类型错误或认证方式不匹配 |
| 403 | 权限不足或实例策略限制 |
| 404 | 路径错误、私有资源不可见；日志也可能尚未生成或已擦除 |
| 3xx API 跳转 | 脚本拒绝携带凭据跟随；先由用户确认规范地址 |
| 429 / 5xx | 限流或服务故障，报告后适度等待；不要无界重试 |
| 200 但不是 JSON | 可能是登录页/反向代理响应，不算 API 成功 |

脚本保持 TLS 验证，继承进程的代理环境；不输出代理值。自签 CA 应走系统/组织信任配置，不加 `--insecure`。HTTP 不会因“内网”自动变成加密连接。

所有脚本使用固定的 GET 端点与字段白名单，不支持任意 URL。版本、角色与项目设置可能影响字段；缺字段不等于零或成功。凭据文件保护依赖 POSIX 权限，原生 Windows 请使用 WSL。
