# R2 反思日志

**反思时间**: 2026-07-06
**测试范围**: 5 个测试用例的结构验证（76 项）+ 6 个端到端模拟场景（31 项）
**测试结果**: 结构验证 76/76 通过；端到端模拟 31/31 通过（修正测试期望后）

---

## 一、R1 修正项验证结果

| R1 修正项 | 验证方式 | 结果 |
|----------|---------|------|
| P1: fde-lead SKILL.md v2.1 完整版 | 结构验证 T1/T2 | ✅ 通过 |
| P2: output_enforcement.yaml 三层降级 | 结构验证 T2 | ✅ 通过 |
| P3: 约束注入执行者 = adapter_code_layer | 结构验证 T3 + 端到端 E3/E4 | ✅ 通过 |
| P4: state_machine.json validation/on_invalid | 结构验证 T4 + 端到端 E1/E2/E6 | ✅ 通过 |
| P5: 飞书 + LangGraph 适配器 | 结构验证 T5 | ✅ 通过 |

---

## 二、R2 测试发现的问题

### 问题 R2-1: 测试期望错误（已修正）

**现象**: E2 测试期望 gate_phase 能直接转到 act/evaluate/done，但 state_machine.json 中 gate_phase 的 transitions 只允许转到 context（用户确认）或 aborted（用户中止）。

**根因**: 测试代码没仔细看 state_machine.json 中 gate_phase 的 transitions 定义。

**修正**: 修改 E2 测试期望：
- gate_phase → context（未确认）→ 应被 skip_without_confirmation 拦截
- gate_phase → act/evaluate/done → 应被 invalid_transition 拦截（不在 transitions 中）
- gate_phase → aborted → 需先产出 abort_reason.json（合理设计）

**状态**: ✅ 已修正，测试全部通过

---

## 三、R2 反思发现的深层设计问题

### 问题 R2-2: C 类平台 state_guard 的被动性（已识别，待缓解）

**问题描述**:
state_guard.py 是被动拦截器，它只能拦截显式的状态转换请求（通过 `commit_transition` 或 `wrap_llm_call` 解析 LLM 输出中的状态转换指令）。但在 C 类平台（Hermes/OpenClaw/Trae/飞书）中：

1. LLM 的输出是自由文本，不是结构化的状态转换请求
2. 如果 LLM 直接输出任务结果（不改变状态，不输出状态转换指令），state_guard 无法拦截
3. state_guard 的 `wrap_llm_call` 方法依赖 LLM 输出中包含可解析的状态转换指令

**影响范围**: C 类平台（Hermes/OpenClaw/Trae/WorkBuddy/飞书）

**缓解方案**: 在 fde-lead SKILL.md 中强制要求 LLM 每次输出包含结构化状态标签，state_guard 解析这个标签。但这属于 prompt 工程，可靠性不如代码层保证。

**与 A/B 类平台的差异**:
- A 类（LangGraph）：原生 StateGraph 引擎强制状态转换，LLM 无法绕过
- B 类（Coze）：workflow 条件节点强制，LLM 无法绕过
- C 类：依赖 state_guard + prompt 工程，可靠性 medium_high（非 highest）

**状态**: ⚠️ 已识别，是 C 类平台的固有限制。缓解方案见 R2 修正 P6。

### 问题 R2-3: output_enforcement.yaml Tier3 实现缺失（已识别，待实现）

**问题描述**:
output_enforcement.yaml 定义了三层降级策略，但 Tier3（prompt+parse+retry）的具体实现没有代码。Tier3 是 Coze/Trae 这类只能用 prompt 的平台的降级策略。

**影响范围**: Coze/Trae/WorkBuddy 平台

**需要的实现**:
1. Tier3 prompt 模板：强制 LLM 输出 JSON 围栏块
2. JSON 解析逻辑：从 LLM 输出中提取 JSON
3. 重试逻辑：解析失败时反馈错误让 LLM 重试

**状态**: ⚠️ 已识别，待 R2 修正 P7 实现。

### 问题 R2-4: 其他平台适配器集成代码缺失（已识别，低优先级）

**问题描述**:
只实现了飞书和 LangGraph 适配器，Coze/Dify/Trae/WorkBuddy 的适配器还没有具体代码。但 base.py 提供了抽象接口，各平台可参照实现。

**状态**: ℹ️ 已识别，低优先级。用户在消息 2 中指定飞书 + LangGraph 作为参考实现，其他平台可后续补。

---

## 四、关键断言自检

本次反思的关键断言验证：

| 断言 | 验证状态 | 说明 |
|------|---------|------|
| state_guard.py 可正确拦截非法状态转换 | ✅ 已验证 | E1/E2/E6 测试通过 |
| 约束合并逻辑正确（硬约束不被覆盖） | ✅ 已验证 | E4 测试通过 |
| 约束块注入到 prompt 末尾 | ✅ 已验证 | E3 测试通过 |
| constraints_injected 标记自动添加 | ✅ 已验证 | E3/E5 测试通过 |
| 8 个 worker agent 都是 v2.1 | ✅ 已验证 | T5 结构验证通过 |
| C 类平台 state_guard 是被动拦截器 | ⚠️ 单源 | 设计分析结论，未经实际 LLM 运行验证 |
| Tier3 实现缺失 | ✅ 已验证 | 代码检查确认无 Tier3 实现 |

---

## 五、R2 修正计划

基于反思发现的问题，R2 修正项：

| 修正项 | 优先级 | 状态 |
|--------|--------|------|
| P6: fde-lead SKILL.md 增加强制状态输出标签（缓解 C 类平台被动性） | P0 | 待实现 |
| P7: 创建 Tier3 prompt+parse+retry 实现 | P1 | 待实现 |
| P8: 其他平台适配器（Coze/Dify/Trae/WorkBuddy） | P2 | 待实现（可后续补） |

---

## 六、与用户原始痛点的对应关系

| 用户痛点 | v2.1 解决机制 | R2 验证结果 |
|---------|-------------|------------|
| 秘书 agent 不调度其他 agent | function calling tool 注册 + state_guard 强制四步流程 | ✅ E1 验证跳过 decide 被拦截 |
| 不按步骤执行 | state_machine.json 程序化状态机 + artifact 检查 | ✅ E1/E6 验证流程顺序 |
| 用户附加要求被忽略 | 适配器代码层注入约束到 prompt 末尾 + QA 审查 | ✅ E3/E4/E5 验证约束注入 |
| Gate 被跳过 | state_guard 拦截 + gate blocking:true | ✅ E2 验证 gate 阻断 |
| 跨平台不兼容 | 三大抽象接口 + 平台适配器 | ✅ T5 验证适配器结构 |
