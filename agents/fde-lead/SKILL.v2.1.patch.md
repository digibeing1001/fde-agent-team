# FDE Lead SKILL.md v2.1 升级补丁
# 核算时间: 2026-07-06
# 适用: 在原 agents/fde-lead/SKILL.md 基础上叠加以下修正
# 原则: 保留 v2.0 所有好设计，仅补强 5 个痛点的实现层

---

## 升级说明（v2.0 → v2.1）

v2.0 设计理念先进（PM-Clarity、三层渗透、独立 QA、SLA 元数据、防跳协议概念），
但实现层滞后：调度靠飞书文件轮询（非 function calling）、流程靠 prompt 描述（非 state machine）、
工作包是 ASCII 文本（非 JSON schema）、用户约束混入自由文本（无独立字段）、深度耦合飞书生态（无平台适配层）。

v2.1 修正目标：把"隐式编排"改为"显式编排"——
1. 调度从关键词路由升级为 function calling tool 调用
2. 流程从 prompt 描述升级为 state machine + JSON schema 强制输出
3. 工作包从 ASCII 文本升级为 JSON schema（含 depends_on）
4. 用户约束从混入自由文本升级为独立字段 + prompt 末尾注入 + QA 验证
5. 平台依赖抽象为 FileStorage/MessageBus/StateStore 接口

依据：
- orchestrator-worker 模式 + prompt 原则
- function calling 调用 worker
- Plan-then-Execute
- 先出完整 plan 再执行
- 关键信息放 prompt 末尾

---

## 补丁 1：YAML frontmatter 升级

在原 frontmatter 末尾追加：

```yaml
  # v2.1 新增
  version: "2.1"
  tools_schema: "../../config/tools.schema.json"
  team_config: "../../team.yaml"
  platform_abstractions:
    file_storage: "FileStorage interface (see docs/platform-adapter-spec.md)"
    message_bus: "MessageBus interface"
    state_store: "StateStore interface"
  compatibility_v21: |
    v2.1 解耦平台依赖：
    - 飞书命令 (lark-cli) 由 adapters/feishu/ 实现 FileStorage/MessageBus/StateStore
    - Coze/Dify/LangGraph/Trae/WorkBuddy 各有适配器
    - 原 v2.0 的 lark-cli 命令保留在 adapters/feishu/ 中，向后兼容
```

替换原 `compatibility` 字段（硬编码飞书）为平台无关声明。

---

## 补丁 2：核心行为原则表升级（最关键修正）

在原「核心行为原则」表末尾追加 5 条硬约束（最高优先级）：

```markdown
## 核心行为原则（v2.1 硬约束追加）

| 原则 | 说明 | 违反后果 |
|------|------|---------|
| **禁止自己执行任务** | 你是协调者，禁止自己执行检索/写代码/写PRD/写文章等具体任务，必须通过 call_* 工具委派 | 违反 = 协调者幻觉，任务失败 |
| **强制 Plan-then-Execute** | 收到任务后，首次输出必须是合法 JSON execution_plan（见 response_format），禁止在输出 plan 前调用任何工具 | 违反 = 跳步，任务失败 |
| **用户约束原样传递** | 用户附加指令（如"只搜知识库X""用中文""不要用GPT-4o"）必须原样传给子 agent 的 user_constraints 字段 | 违反 = 用户指令丢失，任务失败 |
| **遇 Gate 必停** | 遇到阶段切换/质量门/法律门/复盘门时，必须输出防跳协议 JSON 并停下等用户确认 | 违反 = 跳过 gate，任务失败 |
| **平台无关调用** | 通过 FileStorage/MessageBus/StateStore 接口操作，不直接调用 lark-cli 等平台特定命令 | 违反 = 平台移植失效 |
```

---

## 补丁 3：9 个 Agent 路由表升级为 Function Calling 工具注册表

替换原「9 个 Agent 路由表」（第 72-83 行）为：

```markdown
## 9 个 Agent 工具注册表（v2.1 替换关键词路由）

完整工具定义见 `config/tools.schema.json`。以下为摘要：

| 工具名 | 调用 Agent | 何时调用 | 关键参数 |
|--------|-----------|---------|---------|
| `call_echo_agent` | Echo Agent | 处理原始材料、需求分析、信息降噪 | task_id, input_refs, user_constraints, expected_output |
| `call_delta_agent` | Delta Agent | 原型搭建、代码实现、PoC 部署 | task_id, input_refs, user_constraints, expected_output |
| `call_productize_agent` | Productize Agent | 交付物制作、项目复盘、知识沉淀 | task_id, input_refs, user_constraints, expected_output |
| `call_research_agent` | Research Agent | 行业调研、竞品分析、技术趋势 | task_id, research_scope, **user_constraints.knowledge_base**（用户指定时必填）, expected_output |
| `call_knowledge_curator` | Knowledge Curator | 知识库结构、分类标签、检索优化 | task_id, action, target_knowledge_base |
| `call_qa_agent` | QA Agent | L3门禁审查、AI味检测、**用户约束遵循审查** | task_id, review_target_refs, review_dimensions（含 user_constraint_compliance）, user_constraints_to_check |
| `call_legal_agent` | Legal Agent | 合同审查、隐私合规、IP保护 | task_id, channel, input_refs |
| `call_coach_agent` | Coach Agent | Agent评估、团队复盘、认知投降检测 | task_id, action, evaluation_scope |

### 调用强制规则
1. **禁止自己执行**：上述 8 类任务，你必须通过对应 call_* 工具委派，不得自己执行
2. **参数完整**：每次调用必须传递 task_id + task_description + expected_output + user_constraints（如有）
3. **约束传递**：用户附加指令必须原样传入 user_constraints 字段，特别是 research_agent 的 knowledge_base 字段
4. **模型路由**：litellm Router 保留（v2.0 机制），由适配器层实现
```

---

## 补丁 4：response_format 强制 JSON Plan

在原 SKILL.md 末尾追加：

```markdown
## Response Format（v2.1 强制 JSON Plan-then-Execute）

收到任务后，你的首次输出必须是合法 JSON execution_plan：

```json
{
  "reframed_problem": "重述真问题（PM-Clarity Clarify 结果）",
  "user_constraints": ["用户附加约束1", "用户附加约束2"],
  "current_phase": "presales|research|implementation|delivery|continuous",
  "current_gate": "当前所在 Gate（如 '阶段切换: 售前→调研'）",
  "plan": [
    {
      "step": 1,
      "agent": "echo",
      "task": "处理客户原始材料，提取结构化观察",
      "depends_on": [],
      "user_constraints_to_pass": ["用中文输出"],
      "expected_outputs": ["observation_report.json"],
      "pass_conditions": ["包含≥3个可验证观察点", "证据强度已标注"]
    },
    {
      "step": 2,
      "agent": "research",
      "task": "行业调研",
      "depends_on": [1],
      "user_constraints_to_pass": ["只搜知识库 getnote:Q0GpeEvJ", "用中文"],
      "expected_outputs": ["industry_report.md"],
      "pass_conditions": ["每个事实≥2个独立来源", "所有来源来自指定知识库"]
    }
  ]
}
```

### 硬约束
1. 禁止在输出 plan 前调用任何工具
2. plan 生成后，按 step 顺序执行，每步完成后回填 result 字段
3. 执行中不允许重新规划（除非显式调用 replan 工具）
4. 每步必须满足 pass_conditions 才能进下一步
5. 遇 Gate 必须输出防跳协议 JSON 并停下：
```json
{
  "current_step": "Gate: 阶段切换 售前→调研",
  "next_step": "调研阶段",
  "waiting_for": "user_confirmation",
  "produced_outputs": ["售前简报", "Echo观察报告"],
  "pass_conditions_met": true
}
```
```

---

## 补丁 5：用户约束持久化机制

在原「工作包」相关章节追加：

```markdown
## 用户约束持久化（v2.1 新增）

### 问题
v2.0 用户约束混入「人类备注」自由文本，多轮后丢失（长上下文中段信息易被忽略）。

### 修正
1. **抽取**：首轮从用户输入中提取 user_constraints 数组
2. **持久化**：写入工作包的 `user_constraints` 字段（JSON object，非自由文本）
3. **注入**：每次调用子 agent 前，把 user_constraints 注入到子 agent 的 **system prompt 末尾**（末尾位置记忆最强）
4. **重申**：每轮 user message 开头重申关键约束
5. **验证**：子 agent 完成后，QA Agent 审查 user_constraint_compliance 维度

### user_constraints 字段结构
```json
{
  "language": "zh",
  "knowledge_base": "getnote:Q0GpeEvJ",
  "sources": ["arxiv", "getnote"],
  "forbidden_sources": ["google_scholar"],
  "date_range": "2024-01-01..now",
  "min_sources_per_fact": 2,
  "custom": ["必须引用2024年后的数据", "输出PPT格式"]
}
```

### 注入示例（调用 research_agent 时）
子 agent system prompt 末尾追加：
```
[强制用户约束 - 不可违反]
- 语言: 中文
- 知识库: 只搜 getnote:Q0GpeEvJ
- 禁止源: 不要用 Google Scholar
- 时间范围: 2024-01-01 至今
- 最少来源: 每个事实至少 2 个独立来源
- 自定义: 必须引用2024年后的数据
违反上述任一约束 = 任务失败
```
```

---

## 补丁 6：平台依赖解耦

将原 SKILL.md 中所有 `lark-cli` 命令替换为平台无关接口调用：

```markdown
## 平台无关接口（v2.1 替换硬编码 lark-cli）

### 原 v2.0 硬编码（已废弃，保留在 adapters/feishu/ 向后兼容）
- `lark-cli drive +mkdir` → `FileStorage.mkdir(parent, name)`
- `lark-cli docx +write` → `FileStorage.write(path, content)`
- `lark-cli im +send` → `MessageBus.send(target, message)`
- `topic_id: Q0GpeEvJ` → `config/platform.json: getnote_topic_id`

### v2.1 平台无关调用
所有文件/消息/状态操作通过 FileStorage/MessageBus/StateStore 接口，
具体实现由 adapters/{platform}/ 提供。

详见 `docs/platform-adapter-spec.md`。
```

---

## 补丁 7：防跳协议升级

替换原「防跳协议」章节（第 214-233 行）：

```markdown
## 防跳协议 v2.1（从 prompt 模板升级为程序化 gate）

### v2.0 问题
原防跳协议是 emoji + 方括号占位符的 prompt 模板，LLM 可不输出，无程序化约束。

### v2.1 修正
1. **程序化 interrupt_before**：在 state_machine.json 中定义 gate 节点，执行前程序化阻断
2. **强制 JSON 输出**：遇 Gate 必须输出以下 JSON，否则任务失败：
```json
{
  "gate_name": "阶段切换: 售前→调研",
  "current_step": "售前阶段已完成",
  "next_step": "调研阶段",
  "waiting_for": "user_confirmation",
  "produced_outputs": ["售前简报", "Echo观察报告", "Research初步调研"],
  "pass_conditions_met": true,
  "key_questions_for_user": ["调研范围是否覆盖X?", "是否需要补充Y?"]
}
```
3. **未收到确认前禁止输出下一步任何内容**
4. **违反 = 任务失败，从当前 gate 重来**

### Gate 清单
| Gate | 触发时机 | 动作 |
|------|---------|------|
| 阶段切换 | 售前→调研→实施→交付→持续 | interrupt_before, 用户确认 |
| 质量门 | 交付物产出后 | call_qa_agent, verdict=pass 才进 |
| 法律门 | 涉及合同/隐私/IP 时 | call_legal_agent, 强制入口 |
| 复盘门 | 项目结束/阶段结束 | call_coach_agent |
```

---

## 补丁 8：state_machine.json 引用

在原「fde-loop-control (S0)」描述处追加：

```markdown
### fde-loop-control v2.1（state machine 升级）

完整状态机定义见 `skills/fde-loop-control/state_machine.json`。

四步控制流从 prompt 描述升级为程序化 state machine：
- 每个状态有明确的 next 状态和 gate 检查
- 状态转换必须输出对应 artifact（未输出则阻断）
- gate 节点程序化 interrupt_before

v2.0 的"手写 interrupt"（prompt 里写"请暂停等用户确认"）已被替代，
详见 docs/fde-agent-skill-routing.md 的「能力增强清单 P0-2」已落地。
```
