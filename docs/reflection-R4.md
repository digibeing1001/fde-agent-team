# R4 反思日志

**反思时间**: 2026-07-06
**测试范围**: P7 Tier3 输出强制器（33 项）+ 全套回归（159 项）
**测试结果**: P7 33/33 通过；全套 159/159 通过

---

## 一、P7 修正过程回顾

P7 经历 3 轮修复才稳定，符合用户"多反复几次，做稳做好用"的要求。

### 第 1 轮：首次运行 30/33，3 项失败

| 失败测试 | 根因 | 修复 |
|---------|------|------|
| test_json_recovery_unclosed_brackets | 括号补全顺序错误：用两个计数器(open_braces/open_brackets)先补 `}` 再补 `]`，导致 `[}]` 非法 | 改用栈记录开括号顺序，按逆序补全（先内层 `]` 再外层 `}`） |
| test_enforce_with_retry_max_exceeded | attempt=0 首次失败也被记为 parse_retry 并累加 retry_count，导致 count=3 而非 2 | 区分首次失败和重试失败的 count 累加语义 |
| test_compatible_with_state_guard | trae_claude_code 的 parser_strategies 缺 first_object_extract，LLM 输出 JSON+XML 混合文本时无法提取首个 JSON 对象 | 给 trae_claude_code 加 first_object_extract 策略 |

### 第 2 轮：修复后 32/33，新失败

| 失败测试 | 根因 | 修复 |
|---------|------|------|
| test_enforce_with_retry_success_second_try | 把 attempt=0 事件类型改为 "parse_failed" 破坏了测试 24 期望的 "parse_retry" 事件 | 回退：保留每次失败都记录 "parse_retry"，但 count 累加逻辑从 _log_tier3_event 移到调用方显式管理 |

### 第 3 轮：修复后 32/33，又一新失败

| 失败测试 | 根因 | 修复 |
|---------|------|------|
| test_wrap_llm_call_fail | 移除 _log_tier3_event 的 count 累加后，wrap_llm_call 失败不再累加 count，破坏测试 21 期望 count==1 | 在 wrap_llm_call 中显式累加 retry_count（wrap_llm_call 失败表示调用方需要 retry） |

### 第 4 轮：33/33 全部通过

最终事件模型设计：
- **"parse_retry" 事件** = 解析失败，需要 retry（每次失败都记录，保持语义一致）
- **tier3_retry_count 累加规则**：
  - `wrap_llm_call` 失败 → 显式累加（调用方需要 retry）
  - `enforce_with_retry` attempt=0 失败 → 不累加（首次尝试不算 retry）
  - `enforce_with_retry` attempt>0 失败 → 显式累加（真正的 retry）
- **"retry_succeeded" 事件** = retry 后成功
- **"max_retries_exceeded" 事件** = 超过最大重试次数

---

## 二、P7 修正的核心设计决策

### 决策 1: 括号补全用栈而非计数器

**问题**: 两个独立计数器无法表达嵌套关系。`{[}` 这种情况下 open_braces=1, open_brackets=1，但补全顺序应该是 `]}` 而非 `}]`。

**解决**: 用栈记录开括号顺序。遇到 `{` 压入 `}`，遇到 `[` 压入 `]`，遇到闭合括号弹出栈顶。补全时按栈逆序（栈顶是最内层，最先补）。

**依据**: 这是编译原理中括号匹配的标准做法，参考 json-repair 库的实现思路。

### 决策 2: retry_count 语义分层

**问题**: "parse_retry" 事件在 wrap_llm_call 和 enforce_with_retry 中的语义不同：
- wrap_llm_call 失败 = 调用方需要 retry（应该算 1 次 retry）
- enforce_with_retry 首次失败 = 自己会 retry（首次不算 retry）

**解决**: 事件类型保持一致（都是 "parse_retry"），但 count 累加由调用方显式控制。这样事件日志的语义清晰（"parse_retry" = 需要重试），而 count 反映真实的重试负担。

### 决策 3: 所有平台都应有 first_object_extract

**问题**: trae_claude_code 缺 first_object_extract，当 LLM 输出 JSON + XML 状态标签混合文本时无法解析。

**解决**: 给 trae_claude_code 加 first_object_extract。实际上所有平台都应该有这个策略作为兜底，因为它用平衡括号匹配，最能从混合文本中提取 JSON。

**检查**: 5 个平台的 parser_strategies 现在都有 first_object_extract（coze/hermes_openclaw/trae_claude_code/workbuddy/feishu）。✓

---

## 三、当前测试覆盖总览

| 测试套件 | 测试数 | 通过 | 覆盖范围 |
|---------|--------|------|---------|
| R2 结构验证 | 76 | 76 | 工具注册/Plan-Execute/约束持久化/Gate 防跳/跨平台兼容 |
| R2 端到端模拟 | 31 | 31 | 跳过 plan/跳过 gate/约束注入/约束覆盖/完整流程 |
| P6 状态标签 | 19 | 19 | XML 标签解析/missing tag/malformed/no-change/非法转换/JSON 兼容 |
| P7 Tier3 强制器 | 33 | 33 | JSON 解析策略/schema 验证/enforce/wrap/retry/prompt 注入/多平台/StateGuard 兼容 |
| **总计** | **159** | **159** | |

---

## 四、关键断言自检

| 断言 | 验证状态 | 说明 |
|------|---------|------|
| Tier3 JSON 解析的 4 种策略均可用 | ✓ 已验证 | 测试 1-10 覆盖 |
| Schema 验证能检测 missing/wrong_type/enum | ✓ 已验证 | 测试 11-15 覆盖 |
| enforce 方法整合解析+验证 | ✓ 已验证 | 测试 16-19 覆盖 |
| wrap_llm_call 能强制 JSON 输出 | ✓ 已验证 | 测试 20-22 覆盖 |
| enforce_with_retry 支持自动重试 | ✓ 已验证 | 测试 23-25 覆盖 |
| prompt_suffix 注入到末尾且不重复 | ✓ 已验证 | 测试 26-28 覆盖 |
| 5 个平台配置完整且策略合理 | ✓ 已验证 | 测试 29-31 覆盖 |
| Tier3 与 StateGuard 可链式组合 | ✓ 已验证 | 测试 33 覆盖（关键兼容性） |
| 括号补全顺序正确（栈算法） | ✓ 已验证 | 测试 8 覆盖 |
| retry_count 语义正确（首次不算 retry） | ✓ 已验证 | 测试 25 覆盖 |
| trae 平台能处理 JSON+XML 混合输出 | ✓ 已验证 | 测试 33 覆盖 |
| 全套测试无回归 | ✓ 已验证 | 159/159 全通过 |

---

## 五、与用户原始痛点的对应关系

| 用户痛点 | v2.1.1 解决机制 | 验证结果 |
|---------|----------------|---------|
| 秘书 agent 不调度其他 agent | function calling tool 注册 + state_guard 强制四步流程 | ✅ E1/R2-T1 验证 |
| 不按步骤执行 | state_machine.json 程序化状态机 + artifact 检查 | ✅ E1/E6 验证 |
| 用户附加要求被忽略 | 适配器代码层注入约束到 prompt 末尾 + QA 审查 | ✅ E3/E4/E5 验证 |
| Gate 被跳过 | state_guard 拦截 + gate blocking:true | ✅ E2 验证 |
| 跨平台不兼容 | 三大抽象接口 + 平台适配器 | ✅ R2-T5 验证 |
| C 类平台 LLM 输出非结构化 | Tier3 enforcer（P7）+ 状态标签协议（P6） | ✅ P7/P6 验证 |
| 真实 LLM 运行验证 | 待 P9 | ⚠️ 待做 |

**7 项痛点中 6 项已解决，1 项待 P9（真实 LLM 冒烟测试）。**

---

## 六、剩余风险与优先级判断

### 风险 1: 真实 LLM 行为不可预测（中风险）

**现状**: 所有测试都用 mock_llm 模拟，未验证真实 LLM 是否会按 prompt_suffix 输出 JSON、是否会在输出末尾加 `<state_transition>` 标签。

**影响**: 真实 LLM 可能：
- 不遵守 prompt_suffix 指令（概率较低，但有 fallback retry）
- 输出的 JSON 格式更复杂（如带注释、带 BOM、带换行符变体）
- 状态标签格式变体（如 `<state_transition>` vs `<state_transition/>` vs `<STATE_TRANSITION>`）

**缓解**: Tier3 的 4 种解析策略 + retry 机制能兜住大部分情况。但最终需要 P9 真实 LLM 测试确认。

**优先级**: P9（中）- 建议在用户实际导入到某个平台后做冒烟测试。

### 风险 2: 其他平台适配器未实现（低风险）

**现状**: 只实现了飞书和 LangGraph 适配器，Coze/Dify/Trae/WorkBuddy 未实现具体代码。

**影响**: 用户导入到未实现的平台时需要参照 base.py 自行实现。

**缓解**: base.py 提供了完整的抽象接口 + call_worker_agent_wrapper 参考实现。Tier3 enforcer 是平台无关的，所有 C 类平台都可用。

**优先级**: P8（低）- 按需实现，不阻塞核心功能。

### 风险 3: 状态标签与 Tier3 的交互边界（低风险）

**现状**: Tier3 管输出格式（JSON），StateGuard 管状态转换（XML 标签）。两者链式组合时，Tier3 先解析 JSON，StateGuard 再解析 XML 标签。

**潜在问题**: 如果 LLM 把 XML 标签放在 JSON 内部（如 `{"state_transition": {...}}`），两个解析器可能冲突。

**缓解**: P6 测试 13-15 已验证 JSON 内嵌 state_transition/gate/verdict 字段的兼容性。StateGuard 优先解析 XML 标签，JSON 字段作为 fallback。

**优先级**: 无需额外处理，已覆盖。

---

## 七、R4 结论

### 当前状态：稳定可用

- 159/159 测试全部通过
- 7 项用户痛点中 6 项已解决
- P1-P7 修正项全部完成
- 代码层硬保证 + LLM 层软约束的分层防御已建立

### 建议下一步

1. **P9 真实 LLM 冒烟测试**（建议做）：用户选择一个平台（如 Trae/Claude Code 或 Coze）实际导入，跑一个完整的 FDE 流程，验证：
   - FDE Lead 是否输出 JSON execution_plan
   - 状态标签是否在输出末尾
   - 约束是否被注入到子 agent
   - Gate 是否阻断等待确认

2. **P8 其他平台适配器**（按需做）：用户用到哪个平台再实现哪个，base.py 已提供模板。

3. **不再做更多轮反思**：当前测试覆盖充分，核心问题已解决。继续反思的边际收益递减，应转向真实环境验证。
