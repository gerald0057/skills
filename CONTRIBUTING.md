# 维护指南

## 仓库约定

- 每个 Skill 位于 `skills/<skill-name>/`。
- 每个 Skill 必须包含 `SKILL.md`，且 frontmatter 只保留 `name` 和 `description`。
- `name` 必须与目录名一致，只使用小写字母、数字和连字符，长度不超过 64 个字符。
- 详细资料放入 `references/`，可执行工具放入 `scripts/`，输出模板或静态资源放入 `assets/`。
- `SKILL.md` 使用相对路径直接引用需要按需读取的资源，避免多层引用链。
- 不在单个 Skill 中添加 README、安装指南或变更日志；仓库级说明统一维护在根目录。
- `tests/` 中的测试代码属于仓库源文件，必须提交；只忽略测试缓存、覆盖率报告等生成物。

## 新增 Skill

1. 在 `skills/` 下创建与 `name` 同名的目录。
2. 编写 `SKILL.md` 和必要的资源文件。
3. 如需 Codex UI 信息，添加 `agents/openai.yaml`，至少包含 `display_name`、`short_description` 和引用 `$skill-name` 的 `default_prompt`。
4. 更新根目录 README 的 Skill 列表。
5. 为有可执行逻辑的 Skill 在 `tests/` 中增加或更新测试。
6. 运行仓库验证脚本。

```bash
python3 scripts/validate-repository.py
python3 -m unittest discover -s tests -v
```

若本机有 Codex `skill-creator`，同时运行其严格校验：

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py skills/<skill-name>
```

## 发布与版本

- `.codex-plugin/plugin.json` 和 `.claude-plugin/plugin.json` 是整包发布元数据，使用相同的稳定插件名称和版本。
- manifest 的说明、关键词和默认提示只描述集合，不枚举具体 Skill；新增 Skill 时无需机械更新这些文案。
- 对外发布前按语义化版本同时更新两个 manifest。
- 更新 README 中的安装说明，并验证 GitHub 与私有 GitLab 本地安装路径。
- 建议以 `v<version>` 创建 Git tag，方便安装方固定版本。

## 提交前检查

```bash
python3 scripts/validate-repository.py
python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

不要提交采集数据、硬件日志、凭据、用户绝对路径下的私有文件或生成缓存。
