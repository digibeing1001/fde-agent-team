# FDE 耐久运行时

FDE Agent Team 的评分、返工、用户回退和独立 Agent 状态不能只存在于一次模型上下文中。`AtomicJsonStateStore` 为当前 portable kernel 提供标准库实现的单机耐久层，供 `LoopOrchestrator`、`AgentTeamRuntime`、`StateGuard` 和飞书 CLI 共用。

## 它保证什么

- 每个项目使用独立快照，`project_id` 只以 SHA-256 摘要进入文件名，避免路径穿越和项目名称泄漏。
- 写入使用临时文件、`fsync` 和原子替换；同一项目的并发进程通过文件锁串行提交。
- `compare_and_set` 防止过期 worker 覆盖新状态。
- `commit_state_transition` 在一个临界区内提交状态、artifact、幂等索引和事件账本。
- 状态转换事件以 `previous_event_hash → event_hash` 形成可验证哈希链。
- token、模型调用、工具调用与状态迁移预算由代码计数和阻断，不接受模型自报数字。

## 与当前 Loop 配合

```python
from adapters.durable_state_store import AtomicJsonStateStore
from adapters.loop_orchestrator import LoopOrchestrator

store = AtomicJsonStateStore(".fde/runtime")
loop = LoopOrchestrator.from_policy_file(
    store,
    "config/loop-policy.json",
    agent_registry=("echo", "delta", "qa"),
)
```

`LoopOrchestrator` 的步骤状态、评分、返工次数、用户反馈和交付包会通过同一 `StateStore` 协议原子写入。传统状态机路径还可以使用 `StateGuard.commit_transition(..., idempotency_key=...)` 获得事务提交、重放去重和哈希链验证。

## 飞书 CLI 迁移

飞书仍然只显示一个统筹官机器人，专业角色是后端独立 Agent。CLI 参数保持兼容：

```powershell
python -m adapters.feishu.team_cli --state .fde/feishu-team-state.json bootstrap `
  --config config/feishu-team.example.json `
  --name "FDE｜客户项目" `
  --owner-open-id ou_owner
```

`*.json` 参数会映射到同名 `*.d` 原子状态目录。若旧版单文件状态存在，首次启动会迁移全部项目和键，并写入迁移标记；后续启动不会用旧文件覆盖新状态。

## 单机与分布式边界

该实现适用于单机多进程的 Codex、Claude Code、Hermes、OpenClaw、WorkBuddy 和飞书网关。多节点部署必须换用数据库或耐久执行引擎，并保留以下契约：

- 原子 compare-and-set；
- 状态、artifact、幂等索引与事件的事务提交；
- worker lease、崩溃恢复和 schema migration；
- 工具级副作用去重；
- 可验证的事件顺序和租户隔离。

不要把网络共享目录上的本地 JSON 文件锁当作分布式事务。

## 验证

```bash
python tests/p14_durable_state_test.py
```

测试覆盖失败转换不污染状态、纯验证、预算阻断、CAS、幂等冲突、重启恢复、哈希链以及飞书旧状态迁移。
