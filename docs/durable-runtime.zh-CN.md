# FDE 耐久运行时

`StateGuard` 负责判断状态迁移是否合法，`AtomicJsonStateStore` 负责把一次合法迁移作为一个不可分割事务写入本地状态。两者必须配合：只有规则没有耐久存储，会在崩溃或并发 worker 下丢状态；只有存储没有状态守卫，会把非法决策稳定地保存下来。

## 快速使用

```python
from adapters.durable_state_store import AtomicJsonStateStore
from adapters.state_guard import StateGuard

store = AtomicJsonStateStore(".fde-runtime")
guard = StateGuard(
    "agents/fde-lead/skills/fde-loop-control/state_machine.json",
    store,
)

guard.commit_transition(
    "project-42",
    "decide",
    produced_artifact="execution_plan.json (初版)",
    idempotency_key="host-event-0001",
)

# Provider/tool 返回权威用量后由宿主代码记录；不要采用 LLM 自报数字。
guard.record_usage("project-42", tokens=1800, model_calls=1, tool_calls=0)

print(store.snapshot("project-42"))
print(store.verify_transition_log("project-42"))
```

运行目录使用 `project_id` 的 SHA-256 作为文件名，避免路径穿越和在文件名中泄露项目名称。状态写入采用临时文件、`fsync`、原子替换和跨进程文件锁。

## 事务语义

一次 `commit_transition` 同时提交：

- `current_state`；
- 本次确认通过的 artifacts；
- `idempotency_key` 索引；
- 带 `event_id`、`sequence`、`correlation_id`、`causation_id` 的 transition event；
- `previous_event_hash → event_hash` 哈希链。

同一个 `idempotency_key` 重放到同一目标时返回既有结果，不重复增加事件；同一个 key 指向不同目标时 fail closed。验证失败不会写 artifact，这修复了旧实现中“非法迁移虽然被阻断，但伪造 artifact 已进入状态”的污染路径。

## Loop 预算

状态机的 `global_constraints` 现在对 token、model call、tool call 和状态迁移数设置硬上限。`StateGuard` 在继续到非终态前检查这些计数；`wrap_llm_call` 会记录 model call，其他适配器应把 provider 返回的权威 token usage 和真实 tool dispatch 通过 `record_usage` 接入。

预算耗尽后仅允许进入 `failed`、`aborted` 或终态，禁止继续消耗资源。预算不是质量分数；任务仍需独立 QA 与 evidence gate 才能完成。

## 单机与分布式边界

该实现面向单机多进程 Hermes/OpenClaw/WorkBuddy。多节点部署需要数据库或耐久执行引擎，并实现等价契约：

- 原子 compare-and-set；
- 事务内写状态、artifact、idempotency 与 event；
- 崩溃恢复和 worker lease；
- 工具级幂等与副作用去重；
- 可验证的事件顺序及 schema migration。

不要通过网络共享目录把本地 JSON 文件锁伪装成分布式事务。

## 验证

```bash
python tests/p9_durable_state_test.py
```

测试覆盖失败迁移不污染状态、纯验证、原子 CAS、重放去重、幂等冲突、重启恢复、预算阻断及哈希链验证。
