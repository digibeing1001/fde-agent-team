# 多 Agent 与长期 Loop：研究基础和工程取舍

更新日期：2026-07-11

## 结论

多 Agent 不应成为默认模式。当前证据更支持 `solo-first`：先让一个具备必要工具的 Agent 执行；只有任务可独立分解、能力或权限确实异构、存在并行收益，或需要独立 maker-checker 时才升级为团队。相同模型、相同上下文的自由讨论容易增加成本、共识偏差和错误级联。

FDE 因而把“团队”定义为受代码契约约束的异构执行单元，而不是多个角色提示词：

- 角色拥有不同工具、权限、输入和验收责任；
- 交接传 typed artifact 与 evidence reference，不传完整私有推理 trace；
- 状态、预算、终止和人工门由运行时代码控制；
- 反思只能作为待验证假设，不能自动晋升为事实或技能；
- `succeeded` 必须引用测试、环境反馈、文件 hash、工具结果或人工批准。

## 做得好的模式

1. **SOP 与中间产物。** [MetaGPT（ICLR 2024）](https://proceedings.iclr.cc/paper_files/paper/2024/hash/6507b115562bb0a305f1958ccc87355a-Abstract-Conference.html) 显示结构化角色和可执行反馈能改善软件产物，但也增加成本。可借鉴的是依赖明确的 artifacts 和测试反馈，不是无限增加角色。
2. **能力与信任边界分离。** [AutoGen（COLM 2024）](https://openreview.net/forum?id=BAakY1hNKS) 的安全编码与 grounding 实验支持把 writer/executor 与 safeguard/verifier 分开。只有权限或证据源不同的拆分才有工程意义。
3. **环境反馈与经验证技能。** [Voyager](https://arxiv.org/abs/2305.16291) 把课程、可执行技能库、环境错误和自验证组合，并在连续失败后切换任务。FDE 采用“外部证据通过后才能复用”的原则。
4. **有限的反思记忆。** [Reflexion（NeurIPS 2023）](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html) 证明语言反馈在部分任务有效，也报告错误 verifier 会导致退化。反思必须带失败轨迹、版本和验证状态，并限制保留量。
5. **耐久中断与恢复。** [LangGraph 的 interrupt 实现](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/types.py) 明确节点恢复会从节点开头重执行，因此副作用必须幂等；[Microsoft Agent Framework checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints) 在 superstep 边界保存 executor、待处理消息、请求和共享状态。FDE 本地存储实现相同最低语义，分布式场景建议接耐久执行后端。
6. **显式 HITL 和 trace。** [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/) 把工具批准绑定到具体 call ID，并支持序列化后恢复；其[ tracing 文档](https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md) 使用 trace/span/parent 结构。FDE transition event 因而包含 correlation、causation、idempotency 与 hash chain。

## 不应照搬的模式

1. **自由聊天式流水线。** [ChatDev（ACL 2024）](https://aclanthology.org/2024.acl-long.810/) 展示多角色软件开发潜力，也暴露 token 截断、依赖失败和“已经共识但不会结束”。完成信号必须机器可解析，且每轮只处理原子任务。
2. **同质 Agent 堆数量。** [AgentVerse](https://arxiv.org/abs/2308.10848) 中强模型的小幅收益不能外推到所有模型和任务，弱模型团队有时不如 Solo，正确 Agent 还会被错误同伴说服。默认应先独立作答，再按证据聚合。
3. **把投票当验证。** [More Agents Is All You Need](https://arxiv.org/abs/2402.05120) 说明采样投票在部分 benchmark 可扩展，但投票只利用答案分布，不验证现实副作用或事实来源；它适合作为候选聚合，不是 production gate。
4. **无结构辩论。** [Why Do Multi-Agent LLM Systems Fail?（NeurIPS 2025）](https://proceedings.neurips.cc/paper_files/paper/2025/hash/b1041e52d3be19f0a9bc491657488e4a-Abstract-Datasets_and_Benchmarks_Track.html) 在多个框架和轨迹中把失败集中到系统设计、Agent 间失配、验证和终止；仅改角色描述无法解决大部分问题。
5. **把自我反思当事实。** Reflexion 在部分无可靠测试反馈的任务会退化；经验 replay 也可能因不相关信息污染上下文。长期记忆需要 provenance、hash、适用范围、TTL/撤销和人工/外部验证。

## 对 FDE 的直接要求

| 研究风险 | 运行时要求 | 本仓库实现 |
|---|---|---|
| 失败 artifact 污染下游 | 验证纯函数化，成功后事务提交 | `StateGuard` + `AtomicJsonStateStore` |
| 崩溃或并发重复迁移 | CAS、idempotency key、原子 snapshot | `commit_state_transition` |
| 错误历史被重新解释 | 不可变 event、causation、hash chain | `fde-state-transition-event` |
| 无限讨论和成本失控 | call/token/transition 硬预算 | `global_constraints` + `record_usage` |
| 同质团队通信税 | Solo 默认、按异构/并行/验证升级 | 路由与工作包策略 |
| 自我宣布成功 | 独立 evidence/QA gate | `gate_quality`、artifact contract |

## 仍需后续完成

- 建立等 token、等工具、等模型的 Solo / Solo+retry / independent-sampling / Team 对照评测；
- 为分布式宿主实现数据库/Temporal、Dapr 或 Restate 适配器；
- 对 tool sandbox、network egress、secret scope 和 tenant ACL 做真实安全执行层；
- 给长期记忆增加 provenance schema、撤销、TTL、冲突和用户纠正规则；
- 在仓库所有者明确选择开源许可证后添加 `LICENSE`，当前不得推断授权。
