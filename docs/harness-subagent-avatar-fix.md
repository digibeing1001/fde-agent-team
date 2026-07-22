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

- `team.yaml`：为 `secretary` + 8 个 `agents.*` 增加 `avatar: avatars/<id>.png`；顶层新增 `avatar: avatars/team.png`（团队卡片头像真相源）。
- `agents/*/SKILL.md`：frontmatter 增加 `avatar: avatars/<id>.png`（与 `plugin.json` 生成格式一致）。
- `avatars/`：提交 9 张头像 PNG（含领队）+ `team.png`。
- `adapters/workbuddy/avatar_resolver.py`：提供 `resolve_avatar(agent_id)`，输入 team.yaml key
  或 plugin id，输出头像路径。这是宿主修复的**接入点**。

## 3. 宿主侧修复（WB-HARNESS-P0-001 治本项，需 WorkBuddy 团队执行）

专家包仓库无法修改宿主运行时。宿主需做的最小改动 = **在注册 spawned teammate 时，把 `avatar` 一并写进展示元数据**。

### 3.1 接入点
宿主在 `Agent` 工具 / `TeamCreate` spawn 成功、把子代理写入团队 `config.json` 的 `members[]` 处，是唯一的修复点。当前逻辑写入了 `agentId/name/role/agentType/color/backendType/cwd/subscriptions`，唯独漏了 `avatar`。

### 3.2 宿主侧最小补丁（TypeScript 示意）
宿主已有专家包的 `plugin.json` 与 `agents/<id>.md` 定义，按 `subagent_type` 查表即可拿到 avatar：

```typescript
// harness: when registering a spawned teammate
interface TeammateRecord {
  agentId: string;
  name: string;
  role: string;
  agentType: string;
  color: string;
  backendType: string;
  cwd: string;
  subscriptions: string[];
  avatar?: string | null; // ← WB-HARNESS-P0-001: 新增
}

function resolveAvatar(subagentType: string): string | null {
  // 1) 优先查 plugin.json members[]（与顶层专家同一数据源）
  const member = plugin.members.find((m) => m.id === subagentType);
  if (member?.avatar) return member.avatar;
  // 2) 兜底：查 agents/<id>.md frontmatter 的 avatar 字段
  const md = agentMarkdowns.get(subagentType);
  if (md?.avatar) return md.avatar;
  return null; // 未声明时显式返回 null，不抛错（容错）
}

const record: TeammateRecord = {
  agentId,
  name,
  role,
  agentType: subagentType,
  color,
  backendType,
  cwd,
  subscriptions,
  avatar: resolveAvatar(subagentType), // ← 治本：从包定义继承
};
teamConfig.members.push(record);
```

> 若宿主希望复用本仓库的权威解析（team.yaml key / plugin id / agent dir 三种 id 形式、团队头像兜底），
> 可直接移植 `adapters/workbuddy/avatar_resolver.py` 的 `resolve_avatar()` / `team_avatar()` 为宿主语言等价实现。

### 3.3 团队卡片头像
宿主在渲染「团队」卡片时，应从 `plugin.json` 顶层 `avatar`（或 `team.yaml` 顶层 `avatar`，见本仓库 `team_avatar()`）取团队头像，保证领队 / worker / 团队三者一致。

### 3.4 验收标准
- spawn 子代理后，团队 `config.json` 的 `members[]` 出现 `avatar` 字段，且指向存在的 PNG；
- 未声明 avatar 的子代理，`avatar` 为 `null` 而非抛错（容错）；
- UI 中 8 个 worker 与领队、团队卡片均显示头像。

### 3.5 包侧已验证（回归测试）
包侧「单一真相源」契约由 `tests/p9_avatar_resolver_test.py` 锁定：9 个 agent × 3 种 id 形式均可解析、PNG 均存在、团队头像 `avatars/team.png` 存在且 `team_avatar()` 可解析。宿主修复后可直接生效，无需改包数据。

## 4. 复验命令

```bash
python adapters/workbuddy/avatar_resolver.py echo-analyst      # -> avatars/echo-analyst.png
python adapters/workbuddy/avatar_resolver.py fde-lead           # -> avatars/fde-agent-team-team-lead.png
```

## 5. 关联

- `harness_defect_displayname.md`（原验证报告，含命名+头像双不继承）
- `adapters/workbuddy/avatar_resolver.py`（接入点，`resolve_avatar()` / `team_avatar()`）
- `tests/p9_avatar_resolver_test.py`（包侧单一真相源回归测试）
- `team.yaml` / `agents/*/SKILL.md` / `avatars/`（数据源）
