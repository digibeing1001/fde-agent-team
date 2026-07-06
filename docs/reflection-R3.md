# R3 反思日志

**反思时间**: 2026-07-06
**测试范围**: P6 状态标签测试（19 项）+ R2 结构验证（76 项）+ R2 端到端模拟（31 项）
**测试结果**: 全套 126/126 通过

---

## 一、P1-P6 修正项总览

| 修正项 | 解决的问题 | 验证方式 | 结果 |
|--------|-----------|---------|------|
| P1: fde-lead SKILL.md v2.1 | 秘书不调度、跳步、约束丢失 | 结构验证 T1/T2 | ✅ |
| P2: output_enforcement.yaml | response_format 平台差异 | 结构验证 T2 | ✅ |
| P3: 约束注入执行者 = adapter_code_layer | 约束注入靠 LLM 自觉不可靠 | E3/E4 | ✅ |
| P4: state_machine.json validation/on_invalid | gate 阻断靠 prompt 不可靠 | E1/E2/E6 | ✅ |
| P5: 飞书 + LangGraph 适配器 | 无平台无关实现 | T5 | ✅ |
| P6: 强制状态输出标签协议 (v2.1.1) | C 类平台 state_guard 被动性 | P6 测试 19 项 | ✅ |

---

## 二、P6 修正效果验证

### R2-2 问题（C 类平台 state_guard 被动性）的解决效果

**原问题**: state_guard 是被动拦截器，LLM 不输出状态转换指令时无法拦截。

**P6 解决方案**:
1. fde-lead SKILL.md 增加"强制状态输出标签"硬约束，要求 LLM 每次输出末尾带 `<state_transition>` XML 标签
2. state_guard.py 升级支持 XML 标签解析、missing tag 检测、retry 反馈
3. missing tag 超过 max_retries(2) 后记录 warning，状态不变（任务卡住但不会进入错误状态）

**验证结果**:
- ✅ XML 标签解析正确（标准/no-change/属性顺序/带前缀）4 项测试
- ✅ missing tag 检测 + retry 反馈 + violation_log 记录 2 项测试
- ✅ malformed tag 检测 1 项测试
- ✅ no-change 标签不触发转换 1 项测试
- ✅ 非法转换拦截（context→done / gate_phase 未确认）2 项测试
- ✅ 合法转换成功（context→decide / gate_phase 用户确认）2 项测试
- ✅ JSON 兼容（state_transition / waiting_for / verdict）3 项测试
- ✅ wrap_llm_call 集成（合法/no-change/缺 artifact）3 项测试
- ✅ XML 优先于 JSON 1 项测试

**残余风险**: 
- LLM 连续 2 次不输出标签后，任务卡住（状态不变）。这是 C 类平台的固有限制，需要人工介入或更强模型。
- XML 标签协议是 prompt 工程层，可靠性 medium_high（非 highest）。A/B 类平台用原生引擎更可靠。

---

## 三、新发现的问题

### 问题 R3-1: Tier3 prompt+parse+retry 实现仍缺失（R2-3 遗留）

**状态**: ⚠️ 未解决，转入 P7

**影响**: Coze/Trae/WorkBuddy/飞书 平台的 execution_plan JSON 输出无代码层强制。state_guard 只管状态转换标签，不管 execution_plan 是否合法 JSON。

**与 P6 的关系**: 
- P6 解决"状态转换声明"问题（LLM 是否声明要转状态）
- P7 解决"execution_plan 输出格式"问题（LLM 首次输出是否合法 JSON）
- 两者互补，都需要

### 问题 R3-2: 真实 LLM 运行测试缺失

**状态**: ℹ️ 所有测试都是 mock LLM，没有真实 LLM 运行验证

**影响**: 测试验证了代码逻辑正确性，但无法验证 LLM 是否真的遵守 SKILL.md 指令。

**缓解**: 
1. SKILL.md 指令已尽可能明确（硬约束表 + 协议章节 + 正反例）
2. state_guard 代码层兜底（即使 LLM 不遵守也能拦截）
3. 后续可在 Trae 环境做真实 LLM 冒烟测试

### 问题 R3-3: 其他平台适配器未实现（R2-4 遗留）

**状态**: ℹ️ 低优先级，只有飞书和 LangGraph 有完整实现

**影响**: Coze/Dify/Trae/WorkBuddy 适配器需参照 base.py 自行实现。base.py 抽象接口已就绪。

---

## 四、关键断言自检

| 断言 | 验证状态 | 说明 |
|------|---------|------|
| P6 XML 标签解析正确 | ✅ 已验证 | 测试 1-4 通过 |
| P6 missing tag 触发 retry | ✅ 已验证 | 测试 5-6 通过 |
| P6 malformed tag 被拦截 | ✅ 已验证 | 测试 7 通过 |
| P6 no-change 不触发转换 | ✅ 已验证 | 测试 8 通过 |
| P6 非法转换被拦截 | ✅ 已验证 | 测试 9-10 通过 |
| P6 合法转换成功 | ✅ 已验证 | 测试 11-12 通过 |
| P6 JSON 兼容 | ✅ 已验证 | 测试 13-15 通过 |
| P6 wrap_llm_call 集成 | ✅ 已验证 | 测试 16-18 通过 |
| P6 XML 优先于 JSON | ✅ 已验证 | 测试 19 通过 |
| R2 结构验证仍通过 | ✅ 已验证 | 76/76 |
| R2 端到端模拟仍通过 | ✅ 已验证 | 31/31 |
| Tier3 实现缺失 | ✅ 已验证 | 代码检查确认 |
| 真实 LLM 测试缺失 | ✅ 已验证 | 测试都是 mock |

---

## 五、下一步决策

### 决策: 继续 P7（Tier3 实现）

**理由**:
1. Tier3 是 C 类平台（Coze/Trae/WorkBuddy/飞书）execution_plan JSON 输出强制的最后一环
2. 没有 Tier3，C 类平台的 execution_plan 输出可能不合法 JSON，导致后续步骤失败
3. output_enforcement.yaml 已定义 Tier3 配置，只需实现代码

**P7 计划**:
1. 调研: prompt+parse+retry 实现方案（参考 LangChain JsonOutputParser / Instructor 库）
2. 落实: 创建 adapters/tier3_enforcer.py
   - prompt_suffix 注入（从 yaml 加载）
   - JSON 解析（fenced_json / strict_json / json_with_recovery）
   - schema 验证（jsonschema）
   - retry 逻辑（max_retries + 错误反馈）
   - 降级日志（写入 StateStore）
3. 测试: Tier3 降级链路测试

### 优先级排序

| 修正项 | 优先级 | 状态 |
|--------|--------|------|
| P7: Tier3 prompt+parse+retry | P1 | 待实现 |
| P8: 其他平台适配器 | P2 | 待实现（可后续补） |
| P9: 真实 LLM 冒烟测试 | P2 | 待 P7 完成后 |

---

## 六、与用户原始痛点的对应关系（更新）

| 用户痛点 | v2.1 解决机制 | 验证结果 |
|---------|-------------|---------|
| 秘书 agent 不调度其他 agent | function calling tool + state_guard 强制四步流程 | ✅ E1 验证 |
| 不按步骤执行 | state_machine.json + artifact 检查 | ✅ E1/E6 验证 |
| 用户附加要求被忽略 | 适配器代码层注入 + QA 审查 | ✅ E3/E4/E5 验证 |
| Gate 被跳过 | state_guard 拦截 + gate blocking | ✅ E2 验证 |
| 跨平台不兼容 | 三大抽象接口 + 平台适配器 | ✅ T5 验证 |
| C 类平台 LLM 不声明状态转换 | 强制状态输出标签 + missing tag retry | ✅ P6 测试验证 |
| C 类平台 execution_plan 非法 JSON | Tier3 prompt+parse+retry | ⚠️ 待 P7 实现 |
