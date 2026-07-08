# 跨平台部署指南：防代写机械强制配置

> 适用范围：Digital Office Agent System（含 writer-team / research-team / main secretary）、FDE Agent Team。
> 关联协议：C 类平台防代写协议 v2.2（prompt 层）、v2.1.1 state_transition 标签协议、v2.1 function calling 调度。
> 版本：v1.0（2026-07-06）

---

## 0. TL;DR — 三句话决策

1. **v2.2 prompt 协议是 best-effort**：它让 LLM 自省"我是否在代写"，但 LLM 可以撒谎。唯一可靠的是机械强制。
2. **机械强制 = 工具白名单 + workflow DAG + HITL 门**：秘书 Agent 物理上拿不到"写正文"的工具，就代写不了。
3. **平台选型优先级**：Coze/Dify > LangGraph > Claude Code/Trae > Hermes/OpenClaw > WorkBuddy/飞书（最后两个是单 Agent，机械强制最弱）。

---

## 1. 三层防代写框架

防代写（anti-self-authoring）需要三层叠加，缺一不可：

| 层 | 作用 | 实现方式 | 平台依赖 |
|---|---|---|---|
| **L1 机械强制层** | 物理阻止秘书调用产出工具 | workflow DAG / per-Agent tools 白名单 / bindings deny | A/B 类平台原生支持；C 类需 wrapper |
| **L2 工具白名单层** | 秘书只有 dispatch 工具，没有 write_code/write_article 工具 | tools=[...] 字段 / Skill 工具配置 | 全平台支持 |
| **L3 HITL 门层** | 关键节点必须人类确认才推进 | interrupt_before / permission 系统 / HITL 节点 | A 类原生；B 类条件节点；C 类 prompt+wrapper |

**核心原则**：L1 是底线。没有 L1，L2 和 L3 都是 prompt 文字，LLM 可以无视。

### 1.1 失败模式对照

| 失败模式 | 哪层缺失 | 修复 |
|---|---|---|
| 秘书直接写正文（WorkBuddy 实测） | L1 + L2 | 拆 Skill + 工具白名单 |
| 秘书跳过 Gate 直接交付 | L3 | 配置 interrupt_before / HITL 节点 |
| 秘书调用子 Agent 但不传 user_constraints | L1（工作包 schema） | 强制 JSON schema + 适配器注入 |
| 秘书跳过 Plan 直接执行 | L1（state machine） | 配置 execution_plan 必须先于 tool call |

---

## 2. 平台分类与适配矩阵

| 平台 | 类型 | 多 Agent 模式 | 强制机制 | 防代做强度 | 秘书+worker 适配度 |
|---|---|---|---|---|---|
| **LangGraph** | A | StateGraph + Supervisor | 原生 graph 引擎 + interrupt_before | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Dify v1.13+** | A/B | Workflow + Agent 节点 | DAG + 工具白名单 + HITL 节点 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Coze** | B | Workflow 节点编排 | 条件节点 + 工具白名单 + HITL | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **飞书 Aily** | B | Workflow + 智能体节点 | 工具白名单 + MCP 鉴权 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Claude Code / Trae** | C | 主 Agent + Subagent | tools 字段白名单 + permission | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **OpenClaw** | C | Multi-Agent + bindings | bindings allow/deny + sandbox | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Hermes** | C | 单 Agent + Skills | prompt + state_guard wrapper | ⭐⭐⭐ | ⭐⭐⭐ |
| **WorkBuddy** | C | 单 Agent + Skills | prompt + Skill 工具配置 | ⭐⭐⭐ | ⭐⭐⭐ |
| **pi Agent** | C | 单 Agent | prompt only | ⭐⭐ | ⭐⭐ |
| **钉钉悟空** | C | 单 Agent + 六层 Harness | 六层安全体系 | ⭐⭐⭐⭐ | ⭐⭐⭐ |

---

## 3. 平台部署指南（按优先级排序）

### 3.1 Coze（推荐：B 类最佳落地）

**架构**：秘书 Agent 作为 Workflow 入口节点 → 条件分支到 worker 节点 → Gate 节点为 HITL。

**配置步骤**：

1. **建 Workflow**（非单 Agent 对话）：
   - 入口节点 = 秘书 Agent（加载 SOUL.md / 00-secretary.md）
   - 7 个 worker 节点（writer-team）或 9 个（research-team）或 8 个（fde-team）
   - Gate 节点 = 人工审批节点（HITL）

2. **工具白名单**（关键！）：
   - 秘书节点：只给 `dispatch` 工具（或 LLM 节点 + 输出 schema 锁定）
   - 秘书节点**不给** `write_doc` / `write_code` / `search` 等产出工具
   - 每个 worker 节点只给该角色对应的工具

3. **条件节点编排**：
   ```
   秘书节点 → 输出 dispatch JSON → 条件节点解析 action
     ├── action=dispatch → 路由到对应 worker 节点
     ├── action=gate_wait → 路由到 HITL 人工节点
     └── action=report → 路由到输出节点
   ```

4. **HITL 节点配置**：
   - Gate 类型：阶段切换 / 质量门 / 法律门 / 复盘门
   - 审批人：用户（或指定角色）
   - 超时：24h 提醒，48h 再次提醒

5. **v2.2 协议适配**：
   - 秘书节点的输出 schema 锁定为 `dispatch + self_check` JSON
   - 条件节点解析 `dispatch.action` 决定路由
   - 解析 `self_check.am_i_writing_content`，若 `true` 则拒绝并 retry

**验证清单**：
- [ ] 秘书节点无 write_doc/write_code 工具
- [ ] 条件节点能正确解析 dispatch JSON
- [ ] HITL 节点在 Gate 处阻断
- [ ] worker 节点产出后回传到秘书节点

---

### 3.2 Dify v1.13+（A/B 类，DAG + Agent 节点）

**架构**：Workflow + Agent 节点 + HITL 节点。

**配置步骤**：

1. **建 Workflow**：
   - 秘书 Agent 节点（System Prompt = SOUL.md）
   - worker Agent 节点（每个角色一个）
   - HITL 节点（Gate）

2. **工具白名单**（Dify 的 Agent 节点支持 tools 配置）：
   - 秘书 Agent：tools = `[dispatch]`（自定义工具或仅 LLM 节点）
   - worker Agent：tools = 对应角色工具集

3. **DAG 依赖**：
   - 秘书 → worker 的边由 `dispatch.action == "dispatch"` 触发
   - 秘书 → HITL 的边由 `dispatch.action == "gate_wait"` 触发

4. **输出 schema**：
   - Dify v1.13+ 支持 LLM 节点的 structured output
   - 秘书节点的输出 schema = `dispatch + self_check` JSON schema

**验证清单**：
- [ ] 秘书 Agent 节点无产出类工具
- [ ] DAG 边的触发条件正确
- [ ] HITL 节点阻断 Gate
- [ ] structured output schema 锁定

---

### 3.3 LangGraph（A 类金标准）

**架构**：StateGraph + Supervisor + interrupt_before + Checkpointer。

**配置步骤**：

1. **StateGraph 定义**：
   ```python
   from langgraph.graph import StateGraph, END
   
   graph = StateGraph(State)
   graph.add_node("secretary", secretary_node)
   graph.add_node("writer", writer_node)
   graph.add_node("researcher", researcher_node)
   # ... 其他 worker
   graph.add_node("gate_phase", gate_phase_node)
   graph.add_node("gate_quality", gate_quality_node)
   ```

2. **interrupt_before 配置**（关键！）：
   ```python
   graph.compile(
       interrupt_before=["gate_phase", "gate_quality", "gate_legal", "gate_retro"],
       checkpointer=MemorySaver()
   )
   ```
   这样 LLM 无法跳过 Gate 节点——graph 引擎在 Gate 前物理停住。

3. **工具白名单**（关键！）：
   ```python
   secretary_agent = create_react_agent(
       model=llm,
       tools=[dispatch_tool],  # 只有 dispatch，没有 write_doc/write_code
       state_modifier=SOUL_MD
   )
   writer_agent = create_react_agent(
       model=llm,
       tools=[write_doc, edit_doc],
       state_modifier=WRITER_SOUL
   )
   ```

4. **Checkpointer 持久化**：
   - 使用 `MemorySaver()` 或 `SqliteSaver` / `PostgresSaver`
   - Gate 等待期间状态持久化，用户确认后 `Command(resume=...)` 恢复

5. **v2.2 协议适配**：
   - Secretary 节点用 `with_structured_output(dispatch_schema)` 锁定输出
   - Supervisor 解析 `self_check.am_i_writing_content`，若 `true` 则 retry

**验证清单**：
- [ ] interrupt_before 配置在所有 Gate 节点
- [ ] secretary Agent 的 tools 列表无产出工具
- [ ] Checkpointer 持久化正常
- [ ] structured output 锁定 dispatch + self_check

---

### 3.4 Claude Code / Trae（C 类，Subagent + tools 白名单）

**架构**：主 Agent（秘书）+ Subagent（worker），通过 Task tool 调度。

**配置步骤**：

1. **Subagent 定义**（在 `.claude/agents/` 或 Trae 配置中）：
   - 秘书 Subagent：`tools: [Task]`（只有 Task 工具用于调度）
   - writer Subagent：`tools: [Write, Edit, Read]`
   - researcher Subagent：`tools: [WebSearch, WebFetch]`

2. **工具白名单**（关键！）：
   - 秘书的 `tools` 字段**不含** `Write` / `Edit` / `WebSearch`
   - 秘书只有 `Task`（调度）+ `Read`（读上下文）
   - 这样秘书物理上无法写文件或搜索，只能委派

3. **permission 系统**（Gate）：
   - 在 `.claude/settings.json` 配置：
     ```json
     {
       "permissions": {
         "allow": ["Task", "Read"],
         "deny": ["Write", "Edit", "Bash"],
         "ask": ["WebSearch"]
       }
     }
     ```
   - Gate 等待 = permission 系统要求用户确认

4. **v2.2 协议适配**：
   - 秘书的 system prompt 末尾注入 v2.2 协议
   - 每轮输出末尾要求 `dispatch + self_check` JSON 块
   - 无 wrapper 解析时，依赖 LLM 遵守 prompt（best-effort）

**验证清单**：
- [ ] 秘书 Subagent 的 tools 字段无 Write/Edit/WebSearch
- [ ] permission 配置正确
- [ ] v2.2 协议在 system prompt 末尾

---

### 3.5 Hermes / OpenClaw（C 类，bindings + sandbox）

**架构**：Multi-Agent 系统，bindings 层控制工具调用权限。

**配置步骤**：

1. **bindings 配置**（OpenClaw）：
   ```yaml
   agents:
     secretary:
       bindings:
         allow: [dispatch, read_memory, read_knowledge]
         deny: [write_doc, write_code, search_web, send_message]
     writer:
       bindings:
         allow: [write_doc, edit_doc, read_memory]
         deny: [search_web, send_message]
   ```

2. **sandbox scope**：
   - 每个 Agent 运行在独立 sandbox
   - 秘书 sandbox 无文件写入权限
   - writer sandbox 有文件写入权限但无网络权限

3. **state_guard wrapper**（已有，v2.1.1）：
   - 解析 `<state_transition>` XML 标签
   - 拦截非法状态跳转
   - v2.2 补充：可扩展 state_guard 同时解析 `dispatch + self_check` JSON 块

4. **v2.2 协议适配**：
   - 秘书 SOUL.md 已含 v2.2 协议
   - bindings 层 deny 产出工具 = L1 机械强制
   - state_guard 解析 state_transition = 状态机强制
   - 补充：扩展 state_guard 解析 self_check（可选增强）

**验证清单**：
- [ ] secretary bindings deny 产出工具
- [ ] sandbox scope 隔离
- [ ] state_guard 正常拦截非法跳转
- [ ] v2.2 协议在 SOUL.md 中

---

### 3.6 WorkBuddy（C 类，单 Agent + Skills）⚠️ 最弱机械强制

**架构**：单 Agent + Skills 机制。秘书和 worker 都是同一个 Agent 的不同 Skill。

**根本限制**：WorkBuddy 是**单 Agent 系统**，不是多 Agent 编排平台。"多 Agent 并行"= 多窗口多实例，非主 Agent 编排子 Agent。秘书 Skill 物理上能调用所有工具。

**配置步骤（最大化机械强制）**：

1. **拆 Skill**（关键！）：
   - 把 7 个 worker 角色（writer-team）拆成 7 个独立 WorkBuddy Skill
   - 每个 Skill 有独立的工具配置
   - 秘书 Skill 的工具列表**不含** `write_doc` / `write_code`

2. **秘书 Skill 工具配置**：
   - 在 Skill 的 `config.yaml` 中限定工具：
     ```yaml
     tools:
       - read_memory
       - read_knowledge
       - task_management  # 用于 gate_wait 状态跟踪
     forbidden_tools:
       - write_doc
       - write_code
       - bash
     ```

3. **automation SQLite 任务队列**（Gate 替代）：
   - 用 WorkBuddy 的 automation 机制创建定时任务
   - Gate 等待 = automation 任务在指定时间提醒用户确认
   - 用户确认后手动触发下一步 Skill

4. **v2.2 协议适配**：
   - 秘书 Skill 的 system prompt 末尾注入 v2.2 协议
   - 每轮输出末尾要求 `dispatch + self_check` JSON 块
   - **无 wrapper 解析**，完全依赖 LLM 遵守 prompt（best-effort only）

5. **降级策略**：
   - 如果秘书仍然代写，用户需手动干预："停，不要自己写，调用 writer Skill"
   - 或切换到 Coze/LangGraph 平台获得真正的机械强制

**验证清单**：
- [ ] 秘书 Skill 的 forbidden_tools 配置正确
- [ ] v2.2 协议在 Skill 的 system prompt 中
- [ ] automation 任务用于 Gate 等待
- [ ] 用户知晓 WorkBuddy 无原生 DAG，但确认继续路径已接入 `resume_signal`
- [ ] `adapters/workbuddy/workbuddy_adapter.py` 可把用户确认转换为 StateGuard 提交和 `workbuddy_next_payload`

**2026-07 运行时加固**：WorkBuddy 仍是单 Agent 宿主，无法提供 LangGraph/Coze 式原生 DAG；但“用户同意后继续执行”这条链路不再只依赖 prompt。`WorkBuddyResumeAdapter.confirm_and_resume(...)` 会把确认写入 StateGuard、生成 `fde-workbuddy-resume-signal`，并要求宿主直接执行 `next_action`。如果宿主收到 `workbuddy_next_payload.status == "ready"` 后仍输出“我准备如何执行”而不执行，即判定为运行时失败。

**⚠️ 重要警告**：WorkBuddy 平台上 v2.2 协议仍**无法提供原生 DAG 级机械强制**。如果 LLM 不遵守 prompt，秘书仍可能代写。建议：
- 短期：使用 v2.2 协议 + 用户手动监督
- 中期：迁移到 Coze/LangGraph 获得机械强制
- 长期：等待 WorkBuddy 支持 workflow DAG

---

### 3.7 飞书 Aily（B 类，Workflow + 智能体节点）

**架构**：Workflow + 智能体节点 + MCP 鉴权。

**配置步骤**：

1. **建 Workflow**：
   - 秘书智能体节点
   - worker 智能体节点
   - 审批节点（HITL）

2. **工具白名单 + MCP 鉴权**：
   - 秘书节点：只授权 dispatch 相关的 MCP 工具
   - worker 节点：授权对应角色的 MCP 工具
   - 通过 MCP 鉴权控制工具访问

3. **审批节点**：
   - 飞书原生审批流，Gate = 审批节点
   - 审批人 = 用户

**验证清单**：
- [ ] 秘书智能体节点的 MCP 工具白名单
- [ ] 审批节点在 Gate 处阻断
- [ ] v2.2 协议在秘书 system prompt 中

---

## 4. v2.2 协议 prompt 层配置（所有平台通用）

无论平台是否支持机械强制，秘书的 system prompt 末尾都应包含 v2.2 协议。这是 best-effort 层，配合机械强制层使用。

### 4.1 注入位置

v2.2 协议应注入到 system prompt 的**末尾**（末尾位置的指令记忆最强）。

### 4.2 注入内容

各团队的 v2.2 协议已在以下文件中就位：

| 团队 | 文件 | @call_* 适配 |
|---|---|---|
| writer-team | `agents/00-secretary.md` | 7 个：01-选题官/02-研究员/03-大纲师/04-撰稿人/05-审查员/06-风格官/07-排版师 |
| research-team | `profiles/office-research-secretary/SOUL.md` | 9 个：01-PI/02-文献/03-方法/04-工程/05-数据/06-写作/07-评审/08-知识/09-伦理 |
| main | `SOUL.md` | 7 个 portable roles：intake/evidence/planning/product/design/implementation/writing |
| fde-team | `agents/fde-lead/SKILL.md` | 8 个 call_* tools：echo/delta/productize/research/knowledge_curator/qa/legal/coach |

### 4.3 mechanical_enforcement_status 声明

秘书在首轮 `execution_plan` 中必须声明机械强制状态：

```json
{
  "execution_plan": {
    "reframed_problem": "...",
    "mechanical_enforcement_status": "configured | not_configured",
    "mechanical_enforcement_details": "LangGraph interrupt_before + tools whitelist / WorkBuddy prompt-only",
    "plan": [...]
  }
}
```

- `configured`：平台已配置机械强制（L1 + L2 + L3 齐全）
- `not_configured`：仅 prompt 层保护（用户需知晓风险）

---

## 5. 测试清单

### 5.1 通用测试（所有平台）

| # | 测试项 | 通过标准 |
|---|---|---|
| T1 | 秘书收到写作任务后输出 execution_plan | plan 在任何 tool call 之前 |
| T2 | 秘书不自己写正文 | self_check.am_i_writing_content == false |
| T3 | 秘书通过 @call_* 委派 | dispatch.call_tool 不为 null |
| T4 | Gate 处秘书停下 | dispatch.action == "gate_wait" |
| T5 | user_constraints 传递到 worker | worker 收到的 prompt 含约束 |
| T6 | 秘书输出末尾有 dispatch+self_check 块 | JSON 解析成功 |

### 5.2 平台特定测试

| 平台 | 测试项 | 通过标准 |
|---|---|---|
| Coze | 秘书节点无 write_doc 工具 | 工具列表检查 |
| LangGraph | interrupt_before 在 Gate 停住 | graph 在 Gate 前暂停 |
| Claude Code | 秘书 Subagent tools 无 Write | tools 字段检查 |
| WorkBuddy | 秘书 Skill forbidden_tools | config.yaml 检查 |
| Hermes | bindings deny 生效 | 调用被拒绝 |

---

## 6. 故障排查

### 6.1 秘书仍然代写

| 可能原因 | 修复 |
|---|---|
| 机械强制未配置 | 配置 L1（工具白名单 / bindings deny） |
| v2.2 协议未在 prompt 末尾 | 检查注入位置（必须在 system prompt 末尾） |
| LLM 模型能力弱 | 换用更强的模型（Claude Sonnet 4.5+ / GPT-4o+） |
| 上下文过长导致协议被忽略 | 压缩上下文 / 关键信息前置+后置 |

### 6.2 秘书跳过 Gate

| 可能原因 | 修复 |
|---|---|
| 未配置 interrupt_before / HITL 节点 | 配置 L3（HITL 门） |
| state_guard 未拦截 | 检查 state_machine.json 的 transitions 定义 |
| Gate JSON 格式错误 | 检查 secretary 输出的 gate_wait JSON |

### 6.3 user_constraints 丢失

| 可能原因 | 修复 |
|---|---|
| 约束混入自由文本 | 用独立字段 + 适配器自动注入 |
| 注入位置不在 prompt 末尾 | 移到 system prompt 末尾 |
| QA 未审查约束遵循 | 配置 QA Agent 的 user_constraint_compliance 维度 |

---

## 7. 迁移路径建议

### 从 WorkBuddy 迁移到 Coze（推荐）

1. 在 Coze 重建 secretary + worker 为 workflow 节点
2. 配置条件节点解析 dispatch JSON
3. 配置 HITL 节点替代 automation 任务队列
4. 配置工具白名单（ secretary 无产出工具）
5. 导入 v2.2 协议到 secretary 节点的 system prompt

### 从 WorkBuddy 迁移到 LangGraph

1. 用 StateGraph 重建 secretary + worker 节点
2. 配置 interrupt_before 在所有 Gate
3. 配置 Checkpointer 持久化
4. 配置 per-Agent tools 白名单
5. 导入 v2.2 协议到 secretary 的 state_modifier
