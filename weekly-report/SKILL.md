---
name: weekly-report
description: >
  自动解析 iBrain 周报项目看板，生成「总结一下」和「下周计划」章节。
  Use when the user asks to "整理周报", "generate weekly report", 
  or provides a week number like "2026-24".
argument-hint: "[week-number]"
allowed-tools: Read, Edit, Glob, Grep, Bash
---

# weekly-report

Analyze the iBrain weekly report's project board (`[x]`/`[ ]` checkboxes in Section 1) and update Section 3 (总结一下) and Section 4 (下周计划) automatically.

## Arguments

Pass the week identifier as the first argument: `2026-24` (for file `2026-24.md`). When omitted, compute the current ISO week number from today's date.

## iBrain Path

The iBrain repository is at `/home/zhuhy/workspace/projects/docs/iBrain`. Weekly report files live under `00.回顾与展望/01.周记/YYYY/YYYY-WW.md`.

---

## Workflow

### Step 1 — Locate the file

Parse the argument:
- Given `2026-24` → year=2026, week=24 → file: `00.回顾与展望/01.周记/2026/2026-24.md`
- Omitted → compute current ISO week from today's date, construct path the same way

If the file does NOT exist, tell the user and STOP. Do NOT create it automatically (the user may need to create from template first).

### Step 2 — Parse the project board (Section 1)

Extract everything between `# 1 项目看板` and `# 2 支线任务完成情况`. Parse the checkbox tree:

- `- [x] {text}` — completed leaf
- `- [ ] {text}` — pending leaf
- Indentation depth determines parent-child nesting (2-space or tab increments)
- Ignore the YAML code block (` ```base ... ``` `) — that is display configuration, not tasks

Build a tree of nodes. Each node has:
- `text`: task description (strip `[x]`/`[ ]`)
- `completed`: boolean — `[x]` or not
- `children`: array of child nodes
- `level`: nesting depth

**Parent completion rule:**
- A parent with ALL children `completed: true` → treat parent as completed, summarize at parent level
- A parent with SOME children `completed: true` → treat parent as partially-completed; list completed children, mark remaining as pending
- A parent with NO children completed → parent is pending
- If a parent is marked `[x]` but has `[ ]` children → data inconsistency; use children's states and note the anomaly

### Step 3 — Read existing sections 3 and 4

Read the full file to extract:
- Current content of `# 3 总结一下` (to `# 4 下周计划`)
- Current content of `# 4 下周计划` (to `# 5 学习了些什么`)

Purpose: identify external tasks in Section 4 that are not in the project board (preserve them during the merge).

Also parse the **top-level module structure** from Section 1:
- The module grouping is inferred from task hierarchy — look for top-level checkbox items with children (these are modules like "mouse demo", "SmartRF v3.0")
- Map each module to a first-level category (like "GX83xx SDK") — this comes from the `## 1.1 Sagitta SDK` heading or similar headings in the board

### Step 4 — Generate Section 3 (总结一下)

**Strategy: Full regeneration** — regenerate the entire section from scratch based on the current board state. Do NOT attempt incremental edits to the existing summary.

Only generate content for tasks that are completed or partially-completed. If no tasks are completed, write: `本周暂无完成任务。`

**Output format:**

```markdown
# 3 总结一下

{一级模块名}:
1. {二级模块名}
   1. {完成条目1}
   2. {完成条目2}
      - {补充细节}
2. {另一个二级模块}
   1. ...
```

**Rules:**
- Use numbered list: `1.` for first-level modules, indented `1.` for second-level modules, further indented `1.` for individual items
- Each summary item is 1-2 sentences with key results
- Include issue/ticket numbers when present in the task text (e.g., `#439168`, `## 438891`)
- For partially-completed tasks: list completed sub-items normally, then add a final sub-item for pending work with `（待解决）` suffix
- If a task category is unclear, add `⚠️ 需人工确认：{task text}` at the end of its module group
- Be concise — extract the **result/outcome**, not the process

### Step 5 — Generate Section 4 (下周计划)

**Strategy: Incremental merge** — start from the existing Section 4, then:
1. **Remove** items that are now completed in the board (check all `[x]` including newly completed)
2. **Add** items from the board that are `[ ]` (pending) and NOT already in the current plan
3. **Preserve** items in the current plan that don't correspond to any board task (external/learning tasks)

**Output format:**
Same numbered hierarchy as Section 3.
Use semantic field (or similar data inconsistency) → `{task text}` at end of section

```markdown
# 4 下周计划

{一级模块}:
1. {二级模块}
   1. {计划项1}
   2. {计划项2}
2. {另一个二级模块}
   1. ...

{另一个一级模块}：
1. ...
```

**Rules:**
- Merge new pending tasks into their appropriate module group
- If a pending task doesn't fit any existing module, create a new "其他" group under the appropriate first-level module (or add `⚠️ 需人工归类：{task text}` if the module is unclear)
- Preserve external plan items (those not traceable to any board task) as-is at the end of their respective module group

### Step 6 — Write changes

Use the Edit tool to replace:
1. The entire Section 3 content (from `# 3 总结一下` to `# 4 下周计划`) with the generated Section 3
2. The entire Section 4 content (from `# 4 下周计划` to `# 5 学习了些什么`) with the generated Section 4

**IMPORTANT:** Do NOT modify any other sections (1, 2, 5, 6, 7).

### Step 7 — Report

Summarize what was done:
- Number of completed tasks added to Section 3
- Number of items removed from Section 4 (now done)
- Number of items added to Section 4 (newly pending)
- Any ⚠️ items that need manual attention

---

## Error Handling

| Situation | Action |
|-----------|--------|
| File not found | Tell user, suggest creating from template `zz.模板/模板-周记.md`. STOP. |
| No checkbox tasks found | Tell user the board appears empty or has no `[x]`/`[ ]` markers. STOP. |
| Section 1 header not found | Tell user the file structure is unexpected. STOP. |
| Cannot parse a task's module | Include it with `⚠️ 需人工确认` prefix |
| Parent `[x]` with `[ ]` children | Use children's states, add `⚠️` note about inconsistency |
| Existing Section 4 has external tasks | Preserve them — do NOT delete |
| Ambiguous module grouping | Default to existing module structure, flag with `⚠️` |

**Golden rule:** When the automation cannot make a confident decision, surface it with `⚠️` and keep the content for human review. Never silently drop or misplace content.
