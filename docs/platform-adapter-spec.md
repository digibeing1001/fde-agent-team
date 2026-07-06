# FDE Agent Team 平台适配器接口规范 v2.1

**核算时间**: 2026-07-06
**目的**: 解耦平台依赖，使 FDE Agent Team 可导入 Coze/Dify/LangGraph/Trae/WorkBuddy/Hermes 等多平台

## 设计依据

- Anthropic Building effective agents: "用 LLM API 直接实现，许多模式几行代码即可"
- 业界无统一 agent team 定义标准，需自建抽象层
- MCP (Model Context Protocol): 工具定义跨平台可移植

---

## 1. 三大抽象接口

### 1.1 FileStorage（文件存储）

```typescript
interface FileStorage {
  // 读取文件内容
  read(path: string): Promise<string>;

  // 写入文件内容（覆盖）
  write(path: string, content: string): Promise<void>;

  // 创建目录
  mkdir(parent: string, name: string): Promise<string>;

  // 列出目录内容
  list(path: string): Promise<string[]>;

  // 删除文件
  delete(path: string): Promise<void>;

  // 检查是否存在
  exists(path: string): Promise<boolean>;
}
```

**各平台实现**:

| 平台 | 实现类 | 底层 API |
|------|--------|---------|
| Feishu | FeishuStorage | lark-cli drive/docx |
| Coze | CozeStorage | Coze 知识库 API |
| Dify | DifyStorage | Dify 文件节点 |
| LangGraph | LangGraphStorage | checkpoint filesystem |
| Trae | TraeStorage | 本地文件系统 |
| WorkBuddy | WorkBuddyStorage | tdrive |

### 1.2 MessageBus（消息总线）

```typescript
interface MessageBus {
  // 发送消息到目标（agent 或群组）
  send(target: string, message: string): Promise<void>;

  // 轮询消息（按过滤条件）
  poll(filter: MessageFilter): Promise<Message[]>;

  // 订阅消息（流式）
  subscribe(filter: MessageFilter, handler: (msg: Message) => void): Promise<Subscription>;

  // 确认消息已处理
  ack(messageId: string): Promise<void>;
}

interface Message {
  id: string;
  from: string;
  to: string;
  content: string;
  timestamp: string;
  type: "task_assignment" | "task_complete" | "gate_wait" | "error" | "notification";
}

interface MessageFilter {
  from?: string;
  to?: string;
  type?: string;
  since?: string;
}
```

**各平台实现**:

| 平台 | 实现类 | 底层 API |
|------|--------|---------|
| Feishu | FeishuMessageBus | lark-cli im |
| Coze | CozeMessageBus | Coze 消息节点 |
| Dify | DifyMessageBus | Dify workflow 消息传递 |
| LangGraph | LangGraphMessageBus | StateGraph state 传递 |
| Trae | TraeMessageBus | Skill/Task 消息 |
| WorkBuddy | WorkBuddyMessageBus | WorkBuddy 消息系统 |

### 1.3 StateStore（状态存储）

```typescript
interface StateStore {
  // 获取项目级状态
  get(project_id: string, key: string): Promise<any>;

  // 设置项目级状态
  set(project_id: string, key: string, value: any): Promise<void>;

  // 删除状态
  delete(project_id: string, key: string): Promise<void>;

  // 列出项目的所有状态键
  keys(project_id: string): Promise<string[]>;

  // 原子性更新（事务）
  transaction<T>(project_id: string, fn: (store: StateStore) => Promise<T>): Promise<T>;
}
```

**各平台实现**:

| 平台 | 实现类 | 底层 API |
|------|--------|---------|
| Feishu | FeishuStateStore | 飞书文档作为 KV |
| Coze | CozeStateStore | Coze 变量节点 |
| Dify | DifyStateStore | Dify 会话变量 |
| LangGraph | LangGraphStateStore | StateGraph state |
| Trae | TraeStateStore | 内存/文件 |
| WorkBuddy | WorkBuddyStateStore | WorkBuddy 状态系统 |

---

## 2. 适配器目录结构

```
adapters/
├── feishu/                    # 飞书适配器（v2.0 向后兼容）
│   ├── README.md
│   ├── feishu_storage.ts      # FileStorage 实现
│   ├── feishu_message.ts      # MessageBus 实现
│   ├── feishu_state.ts        # StateStore 实现
│   └── install.sh             # 安装脚本（创建飞书文件夹结构）
├── coze/                      # Coze 适配器
│   ├── README.md
│   ├── coze_storage.ts
│   ├── coze_message.ts
│   ├── coze_state.ts
│   └── translate.py           # team.yaml → Coze bot 配置
├── dify/                      # Dify 适配器
│   ├── README.md
│   ├── dify_storage.ts
│   ├── dify_message.ts
│   ├── dify_state.ts
│   └── translate.py           # team.yaml → Dify workflow
├── langgraph/                 # LangGraph 适配器
│   ├── README.md
│   ├── langgraph_storage.ts
│   ├── langgraph_message.ts
│   ├── langgraph_state.ts
│   ├── supervisor.py          # fde-lead 作为 supervisor
│   └── translate.py           # team.yaml → StateGraph 代码
├── trae/                      # Trae 适配器
│   ├── README.md
│   ├── trae_storage.ts
│   ├── trae_message.ts
│   ├── trae_state.ts
│   └── translate.py           # team.yaml → Skill + Task
└── workbuddy/                 # WorkBuddy 适配器
    ├── README.md
    ├── workbuddy_storage.ts
    ├── workbuddy_message.ts
    ├── workbuddy_state.ts
    └── translate.py           # team.yaml → WorkBuddy config
```

---

## 3. 适配器职责

每个平台适配器必须实现：

1. **三大接口实现**：FileStorage / MessageBus / StateStore
2. **翻译器**：把 `team.yaml` + `config/tools.schema.json` 翻译为平台特定配置
3. **安装脚本**：初始化平台资源（文件夹/知识库/bot 等）
4. **回归测试**：跑 `tests/` 下的测试用例，验证 5 个痛点是否解决
5. **用户约束自动注入（v2.1 P3 修正，硬性要求）**：见下文 3.1 节

### 3.1 用户约束自动注入（v2.1 P3 修正）

**问题背景**：v2.0 的设计中，用户约束（如"只搜知识库 X""用中文""不要用 Google Scholar"）由 FDE Lead 的 LLM 负责传递给子 agent。但 LLM 不可靠——多轮对话后约束可能丢失、被改写、被忽略（长上下文中段信息易被忽略）。

**修正原则**：**LLM 决策 + 代码执行分离**
- LLM 只负责决策"调用哪个 agent"（生成 `call_*` 工具调用，参数含 `user_constraints_to_pass`）
- 适配器代码层拦截 `call_*` 工具调用，自动从工作包读取完整 `user_constraints` 并注入到子 agent system prompt 末尾
- 子 agent 不依赖 LLM 自觉传递约束，约束注入是**硬性代码保证**

**实现规范**：

```python
# 伪代码 - 所有平台适配器必须实现等价逻辑
def call_worker_agent(tool_name: str, tool_args: dict, work_package: WorkPackage):
    """
    适配器拦截 call_* 工具调用时的统一入口
    """
    # 1. 从工作包读取完整 user_constraints（不从 LLM 输出读，避免丢失）
    full_constraints = work_package.user_constraints

    # 2. 合并 LLM 在 tool_args.user_constraints_to_pass 中的新约束
    #    （LLM 可以补充本轮新增的约束，但不能覆盖工作包中的硬约束）
    passed_constraints = tool_args.get("user_constraints_to_pass", {})
    merged_constraints = merge_constraints(full_constraints, passed_constraints)

    # 3. 注入到子 agent system prompt 末尾（末尾位置记忆最强）
    sub_agent_prompt = load_role_card(tool_name)
    sub_agent_prompt += format_constraints_block(merged_constraints)

    # 4. 调用子 agent
    result = invoke_sub_agent(sub_agent_prompt, tool_args)

    # 5. 验证子 agent 是否遵守约束（QA Agent 在后续步骤审查）
    return result

def format_constraints_block(constraints: dict) -> str:
    """把约束格式化为 system prompt 末尾的强制约束块"""
    lines = ["\n\n[强制用户约束 - 不可违反]"]
    if constraints.get("language"):
        lines.append(f"- 语言: {constraints['language']}")
    if constraints.get("knowledge_base"):
        lines.append(f"- 知识库: 只搜 {constraints['knowledge_base']}")
    if constraints.get("forbidden_sources"):
        lines.append(f"- 禁止源: 不要用 {', '.join(constraints['forbidden_sources'])}")
    if constraints.get("date_range"):
        lines.append(f"- 时间范围: {constraints['date_range']}")
    if constraints.get("min_sources_per_fact"):
        lines.append(f"- 最少来源: 每个事实至少 {constraints['min_sources_per_fact']} 个独立来源")
    for custom in constraints.get("custom", []):
        lines.append(f"- 自定义: {custom}")
    lines.append("违反上述任一约束 = 任务失败")
    return "\n".join(lines)
```

**各平台实现方式**：

| 平台 | 拦截机制 | 实现位置 |
|------|---------|---------|
| LangGraph | `Command(goto, update)` 的 update 字段 | `adapters/langgraph/supervisor.py` |
| Dify | workflow 节点间变量传递 | `adapters/dify/translate.py` |
| Coze | bot 节点输入参数 | `adapters/coze/translate.py` |
| Trae/Claude Code | Skill 包装器 | `adapters/trae/wrapper.py` |
| WorkBuddy | 配置层注入 | `adapters/workbuddy/translate.py` |
| Hermes/OpenClaw | `adapters/state_guard.py` 拦截 | `adapters/hermes_openclaw/wrapper.py` |
| 飞书 | `adapters/feishu/wrapper.py` 拦截 lark-cli 调用 | `adapters/feishu/wrapper.py` |

**失败模式（fail_closed）**：
- 如果工作包没有 `user_constraints` 字段：允许继续（视为无附加约束）
- 如果适配器无法注入约束：任务失败，记录错误到 StateStore，不让子 agent 无约束执行
- 如果子 agent 输出明显违反约束（如指定中文但输出英文）：QA Agent 在 `user_constraint_compliance` 维度审查时标记 fail

---

## 4. 工具优先用 MCP（跨平台可移植）

知识库检索、文件操作、网络搜索等用 MCP server 实现：

```json
{
  "mcp_tools": [
    {
      "name": "mcp_search",
      "description": "跨平台网络搜索（MCP 标准）",
      "server": "search-mcp-server"
    },
    {
      "name": "mcp_kb_query",
      "description": "知识库查询（支持 getnote/飞书知识库/Coze 知识库）",
      "server": "kb-mcp-server",
      "parameters": {
        "knowledge_base": "string",
        "query": "string"
      }
    }
  ]
}
```

MCP 是跨平台工具标准，Coze/Dify/LangGraph/Trae 均在接入，优先用 MCP 定义工具而非平台内置工具。

---

## 5. 跨平台兼容性 Checklist

导入到新平台时，检查以下项：

- [ ] **核心 prompt 独立成文件**：agents/*/SKILL.md 不嵌入平台特定配置
- [ ] **工具用 MCP 或 JSON schema 定义**：config/tools.schema.json，不依赖平台内置工具
- [ ] **流程用 state_machine.json 描述**：agents/fde-lead/skills/fde-loop-control/state_machine.json
- [ ] **角色边界用 role card 显式声明**：每个 SKILL.md 末尾有 boundary
- [ ] **用户附加指令通过 user_constraints 字段注入**：不依赖平台特定变量传递
- [ ] **平台特定值在 config/platform.json**：不硬编码在 SKILL.md
- [ ] **三大接口已实现**：FileStorage / MessageBus / StateStore
- [ ] **翻译器已运行**：team.yaml → 平台特定配置
- [ ] **回归测试通过**：tests/ 下 5 个测试用例全部 pass

---

## 6. 与 v2.0 的向后兼容

v2.1 保持对 Hermes/OpenClaw 的向后兼容：

- `agents/fde-lead/SKILL.md` 保留为 Hermes/OpenClaw 的 entrypoint
- 原 `lark-cli` 命令保留在 `adapters/feishu/` 中
- `config/platform.json: active_adapter = "feishu"` 时，使用 FeishuStorage/FeishuMessageBus/FeishuStateStore
- 其他平台切换 `active_adapter` 即可
