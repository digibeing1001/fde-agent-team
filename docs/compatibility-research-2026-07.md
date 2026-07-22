# 主流 Agent 宿主兼容性调研（2026-07）

## 调研问题

本次调研不是比较模型回答质量，而是回答一个更具体的问题：**哪些 Agent 工具能够运行一支由统筹官动态组建、拥有独立执行角色、逐步评分返工、持久状态和用户反馈闭环的 FDE 团队？**

统一评估八项不可降级能力：

1. 独立 Agent 实例；
2. 隔离上下文；
3. 动态委派；
4. 持久状态；
5. 确定性的逐步评分门；
6. 用户接受/拒绝反馈循环；
7. 完整审计轨迹；
8. Agent 级工具权限。

## 结论

没有一个交互式 Agent 产品可以仅靠角色提示词稳定满足全部八项要求。主流产品的强项大致分为三类：

- **原生团队/子 Agent 宿主**：负责创建隔离 Agent、并行工作和消息传递；
- **可编程 Agent 框架**：负责图编排、状态、检查点、HITL 和追踪；
- **后台/并行任务产品**：适合同时推进多个独立任务，但不等于拥有共享任务板和逐步质量门。

因此本项目采用“**宿主负责执行，FDE portable kernel 负责一致性**”的架构。宿主能力再强，也不能绕过 `LoopOrchestrator` 的评分、返工、用户回退和审计；宿主能力不足时，通过 JSON 工作信封和独立进程桥补齐，而不是用 prompt 假装通过。

## 适配优先级

### P0：直接面向用户的核心宿主

| 宿主 | 官方多 Agent 能力 | 适配策略 | 主要限制 |
|---|---|---|---|
| Claude Code | Agent Teams 提供独立 teammate、共享任务列表和 mailbox；subagent 有隔离上下文 | 原生团队 + portable kernel | Agent Teams 仍是实验功能，恢复和关闭有已知限制 |
| OpenAI Codex | App 支持多线程并行 Agent 和隔离 worktree；支持 AGENTS.md/skills | 并行线程 + portable kernel | 公开资料没有把仓库内“团队定义”规定为统一稳定协议 |
| Gemini CLI | 项目级自定义 subagent、独立上下文、工具隔离和策略引擎 | 原生 subagent + portable kernel | subagent 属 preview，不能嵌套调用其他 subagent |
| GitHub Copilot CLI / SDK | custom agents、隔离 subagent、并行和生命周期事件 | 原生 subagent + portable kernel | CLI、SDK、Cloud Agent 的 profile 能力并不完全相同 |
| OpenCode | primary/subagent、Task 委派、独立 child session、Agent 级权限 | 原生 subagent + portable kernel | 持久评分状态需要外部运行时 |
| OpenClaw | 多个隔离 Agent、SQLite session、`sessions_spawn` 后台 subagent、工具策略 | 原生多 Agent + portable kernel | announce 为 best-effort，共享 Gateway 资源 |
| Hermes Agent | `delegate_task` 创建隔离终端会话并支持并行子 Agent | 原生委派 + portable kernel | 委派同步且不耐父回合中断，持久长任务需要 cronjob 等机制 |
| WorkBuddy | 当前仓库已有 teammate、resume、插件和头像适配证据 | feature probe + portable kernel | 缺少稳定公开的完整宿主规范，必须按安装版本现场探测 |

### P1：推荐的编排后端和扩展宿主

| 平台 | 适合程度 | 原因 |
|---|---|---|
| Microsoft Agent Framework | 很高 | 图工作流、checkpoint、HITL、顺序/并行/交接/群聊/Magentic 与遥测齐全 |
| Google ADK | 很高 | graph/dynamic workflows、顺序/循环/并行 Agent、Session/State/Memory 和 A2A |
| LangGraph / LangChain Agents | 很高 | supervisor/subagent、自定义图、interrupt、checkpointer 和持久子图 |
| OpenAI Agents SDK | 高 | agents-as-tools、handoff、guardrails、session 和内建 tracing |
| CrewAI | 高 | Crew 提供自治团队，Flow 提供状态、条件、循环和确定性控制 |
| Cursor | 中 | Background Agent API 可并行运行隔离任务，但共享团队语义需 portable kernel |

### P2：工作流/API 桥

Dify 适合把每个独立 Agent 暴露为 API 节点并由工作流连接。多个 LLM 节点本身不应被宣称为完整独立 Agent 团队；实例身份、评分返工和用户回退仍由 portable kernel 管理。

## 官方证据

- Claude Code：[Agent Teams](https://code.claude.com/docs/en/agent-teams)、[Subagents](https://code.claude.com/docs/en/sub-agents)
- OpenAI Codex：[Codex App 多 Agent 与 worktree](https://openai.com/index/introducing-the-codex-app/)、[Codex 并行任务与 AGENTS.md](https://openai.com/index/introducing-codex/)
- Gemini CLI：[Subagents](https://geminicli.com/docs/core/subagents/)、[Policy Engine](https://geminicli.com/docs/reference/policy-engine/)
- GitHub Copilot：[CLI Custom Agents](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-custom-agents)、[SDK Sub-agent Orchestration](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/custom-agents)
- OpenCode：[Agents](https://opencode.ai/docs/agents/)
- OpenClaw：[Multi-agent Routing](https://docs.openclaw.ai/concepts/multi-agent)、[Sub-agents](https://docs.openclaw.ai/tools/subagents)
- Hermes Agent：[Delegation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md)
- Cursor：[Background Agents](https://docs.cursor.com/background-agent)、[Background Agents API](https://docs.cursor.com/background-agent/api/overview)
- OpenAI Agents SDK：[Agent Orchestration](https://openai.github.io/openai-agents-python/agents/)、[Tracing](https://openai.github.io/openai-agents-python/tracing/)
- LangGraph：[Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)、[Custom Workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)
- Google ADK：[Workflow Agents](https://adk.dev/agents/workflow-agents/)、[Session/State/Memory](https://adk.dev/sessions/)
- Microsoft Agent Framework：[Overview](https://learn.microsoft.com/en-us/agent-framework/overview/)、[Workflow Orchestrations](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/)
- CrewAI：[官方文档](https://docs.crewai.com/)
- Dify：[Workflow Quick Start](https://docs.dify.ai/en/guides/application-orchestrate/creating-an-application)

## 兼容性等级的含义

- **契约兼容**：官方能力加 portable kernel 后覆盖八项硬契约，并通过仓库 conformance tests。
- **原生能力**：产品官方明确提供，不代表已经实现 FDE 的评分规则。
- **现场验证**：必须在具体产品版本、权限、模型和操作系统上运行 smoke test。本文档不会把“读过官方文档”冒充成“所有版本已现场认证”。

机器可读矩阵位于 `config/host-capabilities.json`；`python -m adapters.compatibility.cli matrix --json` 可查看实时仓库判定。
