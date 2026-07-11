# FDE Agent Team

## 2026-07 耐久 Loop 运行时增强

本版补上了长期 loop 真正需要的执行语义，而不只是在 prompt 里声明“可恢复”：

- `AtomicJsonStateStore`：跨进程文件锁、CAS、原子替换与重启恢复；
- 状态 + artifact + idempotency + transition event 在同一事务提交；
- 失败迁移不再污染 artifact 状态；
- transition event 带 sequence/correlation/causation 和可验证哈希链；
- token/model/tool/transition 硬预算在代码层阻断继续消耗；
- GitHub Actions 在 Python 3.11/3.12 执行 6 组确定性测试。

生产用法和单机/分布式边界见 [FDE 耐久运行时](docs/durable-runtime.zh-CN.md)，论文依据与取舍见 [多 Agent 与长期 Loop：研究基础](docs/research-foundations-2026-07.zh-CN.md)。

## WorkBuddy 继续执行增强

2026-07 更新：新增 `adapters/workbuddy/workbuddy_adapter.py`，把 WorkBuddy 中的“用户已同意继续”转换为 StateGuard 提交、`resume_signal` 和可执行 `workbuddy_next_payload`。当用户确认下一步后，宿主应直接执行 `next_action`，而不是让 Agent 再回答“准备如何执行”后停下。

> 一支按 Palantir FDE（前置部署工程师）模式组织的 AI Agent 团队，卖结果不卖软件——你给目标，团队按流程交付。

---

## 给谁用

- **想落地 AI Agent 的创业团队**：有想法但缺人手，需要一支能从调研到交付全流程跑通的 AI 团队
- **企业 IT / 数字化负责人**：需要把 AI 能力落地到具体业务场景，不是聊天 demo，是真能交付结果
- **AI 工程师 / 全栈开发者**：想要一套可复用的 Agent 编排框架，有分工、有门控、不瞎跑
- **咨询顾问 / 解决方案架构师**：需要快速调研、出方案、做原型、交付文档，按阶段推进

---

## 作用是什么

把一支「前置部署工程师团队」装进你的电脑——有 Lead 统筹，有感知/执行/交付/调研/知识/质检/法务/复盘 8 个专业角色，按固定流程接力干活。

跟普通 AI 工具最大的不同：**普通 AI 你问一句答一句，答完就散了；这套团队有防代写协议——Lead 只负责调度不负责干活，每一步必须委派给专业 Agent 执行，每道关卡必须停下来等你确认。**

---

## 功能有哪些

### 全流程覆盖

| 你遇到的问题 | 团队怎么帮你 |
|---|---|
| 「我想做 XX 但不知道从哪下手」 | FDE Lead 用苏格拉底式追问把模糊想法拆成具体任务，出 execution_plan |
| 「需要调研行业/竞品/技术趋势」 | Research Agent 做行业调研、竞品分析、技术趋势追踪 |
| 「有想法但没原型」 | Delta Agent 做技术选型、写代码、搭原型，做逆向检查 |
| 「做完了不知道交付什么」 | Productize Agent 做交付物、复盘、知识沉淀、模板化 |
| 「需求不清楚，总在变」 | Echo Agent 做感知、需求分析、信息去噪，把模糊变清晰 |
| 「知识库乱成一锅粥」 | Knowledge Curator 管分类体系、标签规则、归档清理 |
| 「交付物质量没底」 | QA Agent 做七维审查（质量/红线/AI 味/完整性/测试覆盖/版本对比/约束遵循） |
| 「怕合规出问题」 | Legal Agent 做合同审查、隐私数据、产品合规、IP 和争议分流 |

### 九个角色

| 角色 | 类型 | 干什么 | 边界 |
|---|---|---|---|
| FDE Lead | 编排者 | 接活、出 plan、调度、盯 Gate | 禁止自己执行任何具体任务 |
| Echo Agent | 生产者 | 感知、需求分析、信息去噪 | 仅做感知分析，不做执行 |
| Delta Agent | 生产者 | 技术选型、写代码、搭原型 | 仅做执行，不做感知审查 |
| Productize Agent | 生产者 | 交付物、复盘、知识沉淀 | 仅做交付，不做执行审查 |
| Research Agent | 生产者 | 行业调研、竞品分析、技术趋势 | 仅做调研，不做执行 |
| Knowledge Curator | 生产者 | 分类体系、标签规则、归档清理 | 管结构不产内容 |
| QA Agent | 守门人 | 七维质量审查 | 独立于生产链，自己审自己是致命缺陷 |
| Legal Agent | 守门人 | 合同、隐私、合规、IP、争议 | 输出审查草稿，不替代执业律师 |
| Coach Agent | 守门人 | 评估、复盘、审计、认知投降检测 | 不参与交付，仅做评估 |

### 四道门控

| 门控 | 触发时机 | 做什么 |
|---|---|---|
| 阶段切换门 | 售前→调研→实施→交付→持续 | 必须用户确认才进下一阶段 |
| 质量门 | 交付物产出后 | QA Agent 审查通过才进下一步 |
| 法律门 | 涉及合同/隐私/IP 时 | Legal Agent 强制介入 |
| 复盘门 | 项目/阶段结束 | Coach Agent 做复盘评估 |

---

## 优势是什么

### 1. 防代写协议 v2.2——Lead 只调度不干活

这是这支团队最核心的设计。Lead 接到任务后**必须先出 execution_plan（JSON 格式），再按 plan 逐步委派给专业 Agent**。四条硬约束：

1. 输出格式锁：每步必须输出 `当前步骤 / 下一步 / 等待确认 / 已产出 / 通过条件`
2. @call_* 占位符：Lead 只能调用 `call_*` 委派工具，不能直接执行任何任务
3. self_check 四问：每步自检「我是在委派还是在代写」「我跳步了吗」「用户约束传了吗」「Gate 通过了吗」
4. 第一轮强制 Plan：首次输出必须是合法 JSON execution_plan，禁止在输出 plan 前调用任何工具

**违反任一条 = 任务失败，从当前步骤重来。**

### 2. 机械强制层——不只靠 prompt，靠代码保证

prompt 层的约束可能被 LLM 忽略，所以加了三层机械强制：

- **L1 状态机**（state_guard.py）：非法状态跳转直接阻断，LLM 说了不算
- **L2 工具白名单**（tools.schema.json）：Lead 只可见 call_* 委派工具，执行类工具对 Lead 不可见
- **L3 HITL 门**：Gate 处程序化 interrupt_before，不是靠 prompt 提醒

### 3. 用户约束持久化——你说的话不会丢

你给 Lead 的附加指令（「必须用国产模型」「预算不超过 X」「必须包含某案例」）会：
- 存入工作包的 user_constraints 字段
- 由适配器代码层自动注入到每个子 Agent 的 prompt 末尾（不依赖 LLM 自觉传递）
- QA Agent 专门审查约束遵循情况
- 注入失败 = 任务失败（fail_closed），不让子 Agent 无约束执行

### 4. 平台无关——一套定义，多平台适配

team.yaml 是单一真相源，各平台适配器负责翻译：
- A 类平台（LangGraph / Claude Code / Microsoft Agent Framework 等）：直接读 team.yaml + tools.schema.json
- B 类平台（Coze / 飞书 Bot / 腾讯元器）：适配器翻译为平台特定格式
- C 类平台（Hermes / OpenClaw / Trae / WorkBuddy）：适配器用 v2.2 协议在 prompt 层最佳努力执行

### 5. 耐久状态——崩溃恢复、并发安全、可重放

- 本地运行时以项目级原子 snapshot 保存状态，进程重启后可以继续读取；
- 同一宿主事件用 `idempotency_key` 去重，重复投递不会重复迁移；
- 并发 worker 用 compare-and-set 阻止过期状态覆盖；
- 哈希链 transition log 可以验证事件有没有被改写或乱序；
- 单机 JSON store 不冒充分布式数据库，集群部署必须替换成等价事务后端。

---

## 特点是什么

- **有分工**：9 个角色各管一摊，Lead 只调度不干活，专业的事交给专业的 Agent
- **有防代写**：四条硬约束 + self_check 四问，Lead 不能自己动手写东西
- **有机械强制**：状态机阻断非法跳转 + 工具白名单限制 Lead 能力 + 程序化 HITL 门
- **有门控**：四道关卡（阶段切换/质量/法律/复盘），过不了就停下
- **有约束持久化**：用户附加指令代码层注入，不靠 LLM 自觉
- **有四步干活法**：context → decide → act → evaluate，不会无限循环
- **平台无关**：一套定义适配多个平台，不绑死某个工具
- **可观测**：全链路 trace，每步谁干的、花了多少、有没有失败，查得到
- **可恢复**：状态迁移、artifact、幂等索引和证据事件原子提交，失败不留半成品状态
- **有预算**：model/tool/token/transition 硬上限由代码检查，防止讨论或反思无限循环

---

## 怎么用

### 第一步：克隆仓库

```bash
git clone https://github.com/digibeing1001/fde-agent-team.git
cd fde-agent-team
```

### 第二步：选择你的平台

| 平台类型 | 代表平台 | 怎么用 |
|---|---|---|
| A 类（原生支持） | LangGraph / Claude Code / Microsoft Agent Framework | 直接读 team.yaml + tools.schema.json |
| B 类（适配翻译） | Coze / 飞书 Bot / 腾讯元器 | 用 adapters/ 下的适配器翻译 |
| C 类（prompt 层） | Hermes / OpenClaw / Trae / WorkBuddy | 用 v2.2 协议在 prompt 层最佳努力执行 |

### 第三步：跟 FDE Lead 说一句话

```text
帮我调研一下市面上的 AI Agent 编排框架，出一版选型建议。
```

```text
我想做一个智能客服 Agent，从需求到原型帮我走一遍。
```

Lead 不会立刻动手，会先出 execution_plan（JSON），列出每一步交给谁、产出什么、Gate 在哪，你确认后才开干。

---

## 目录结构

```
fde-agent-team/
├── team.yaml                          # 平台无关单一真相源
├── agents/                            # 9 个角色
│   ├── fde-lead/SKILL.md              # Lead 角色卡（含 v2.2 防代写协议）
│   ├── echo-agent/SKILL.md            # 感知分析
│   ├── delta-agent/SKILL.md           # 执行原型
│   ├── productize-agent/SKILL.md      # 交付复盘
│   ├── research-agent/SKILL.md        # 调研
│   ├── knowledge-curator/SKILL.md     # 知识管理
│   ├── qa-agent/SKILL.md              # 质量审查
│   ├── legal-agent/SKILL.md           # 法务审查
│   └── coach-agent/SKILL.md           # 评估复盘
├── adapters/                          # 平台适配器
│   ├── base.py                        # 适配器基类
│   ├── durable_state_store.py         # 原子状态、CAS、幂等与哈希链事件
│   ├── tier3_enforcer.py              # C 类平台 v2.2 协议强制器
│   └── state_guard.py                 # 状态机守卫
├── config/                            # 配置
│   ├── tools.schema.json              # 工具白名单（tools_allow / tools_deny）
│   └── output_enforcement.yaml        # 输出强制策略
├── docs/                              # 文档
│   ├── platform-adapter-spec.md       # 平台适配器接口规范
│   ├── durable-runtime.zh-CN.md        # 耐久运行时使用与边界
│   ├── research-foundations-2026-07.zh-CN.md # 论文和开源实践依据
│   └── cross-platform-deployment-guide.md  # 跨平台部署指南
└── tests/                             # 6 组确定性测试（168 项全部通过）
```

---

## 更多文档

- [team.yaml](team.yaml) —— 平台无关单一真相源，团队定义的权威文件
- [docs/platform-adapter-spec.md](docs/platform-adapter-spec.md) —— 平台适配器接口规范
- [docs/cross-platform-deployment-guide.md](docs/cross-platform-deployment-guide.md) —— 跨平台部署指南（A/B/C 类平台）
- [docs/durable-runtime.zh-CN.md](docs/durable-runtime.zh-CN.md) —— 原子状态、幂等、预算和事件回放
- [docs/research-foundations-2026-07.zh-CN.md](docs/research-foundations-2026-07.zh-CN.md) —— 多 Agent/长期 loop 的论文依据和工程取舍
- [config/tools.schema.json](config/tools.schema.json) —— 工具白名单配置
- `agents/fde-lead/skills/fde-loop-control/state_machine.json` —— 状态机定义

---

## License 与说明

- 所有自写的角色文档和技能文件为原创
- 方法论仅作设计参考，未复制代码
- 当前仓库尚未包含 `LICENSE`；在所有者明确选择许可证前，默认不授予第三方复制、修改或再分发权，请勿把“公开可见”误认为“已开源授权”
- Legal Agent 输出审查草稿，**不替代执业律师**，最终外发仍需人类律师把关
