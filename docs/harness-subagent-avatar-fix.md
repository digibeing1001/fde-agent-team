# WorkBuddy 子代理头像修复（WB-HARNESS-P0-001）

状态：子代理头像不显示 — **根因在 WorkBuddy 宿主运行时，不在专家包数据**。
影响：spawn 出的 8 个 worker 子代理在 UI 中无头像（领队「首席项目统筹 凌航远」有头像，因其来自 `plugin.json` 顶层 `members[0].avatar`）。

---

## 1. 实证验证（2026-07-23）

测试：用 `TeamCreate` 建队，以 `subagent_type=echo-analyst` + `name="感知分析师 温其真"` spawn 一名子代理，
随后读取宿主写入的团队 `config.json`。

结果：`members[]` 中子代理记录含 `agentId / name / role / agentType / color / backendType / cwd / subscriptions`，
**唯独没有 `avatar` 字段**。

对照：专家包内 `plugin.json` 的 `members[]` 与每个 `agents/<id>.md` frontmatter 均已正确声明 `avatar`
（如 `avatars/echo-analyst.png`，文件真实存在）。

结论：**头像数据完整且正确；宿主在 spawn 子代理时未把 avatar 注入到子代理的展示元数据。**
这与 WB-HARNESS-P0-001 一致——宿主为顶层专家读 `members[].avatar`，但不回查 spawn 子代理的 avatar。

## 2. 专家包侧修复（本仓库已落地）

让专家包成为「头像数据单一真相源」，宿主一旦修复即可直接生效：

- `team.yaml`：为 `secretary` + 8 个 `agents.*` 增加 `avatar: avatars/<id>.png`。
- `agents/*/SKILL.md`：frontmatter 增加 `avatar: avatars/<id>.png`（与 `plugin.json` 生成格式一致）。
- `avatars/`：提交 9 张头像 PNG（含领队）+ `team.png`。
- `adapters/workbuddy/avatar_resolver.py`：提供 `resolve_avatar(agent_id)`，输入 team.yaml key
  或 plugin id，输出头像路径。这是宿主修复的**接入点**。

## 3. 宿主侧修复（WB-HARNESS-P0-001 治本项，需 WorkBuddy 团队执行）

专家包仓库无法修改宿主运行时。宿主需做的最小改动：

> 在注册 spawned teammate（Agent 工具 / TeamCreate spawn）时，依据 `subagent_type`
> （或 `agentId`）调用包内 `resolve_avatar()`，将返回的路径写入该 teammate 的展示元数据
> （团队 `config.json` 的 `members[].avatar`，以及 UI 头像字段）。

伪代码：

```python
avatar_path = resolve_avatar(subagent_type)          # 或 plugin 包内等价解析
team_config.members.append({
    "agentId": ..., "name": ..., "agentType": subagent_type,
    "avatar": avatar_path,                            # ← 新增：从包定义继承
    ...
})
```

验收标准：spawn 子代理后，团队 `config.json` 的 `members[]` 出现 `avatar` 字段且指向存在的 PNG，
UI 中 8 个 worker 与领队一样显示头像。

## 4. 复验命令

```bash
python adapters/workbuddy/avatar_resolver.py echo-analyst      # -> avatars/echo-analyst.png
python adapters/workbuddy/avatar_resolver.py fde-lead           # -> avatars/fde-agent-team-team-lead.png
```

## 5. 关联

- `harness_defect_displayname.md`（原验证报告，含命名+头像双不继承）
- `adapters/workbuddy/avatar_resolver.py`（接入点）
- `team.yaml` / `agents/*/SKILL.md` / `avatars/`（数据源）
