"""
FDE Agent Team - LangGraph 适配器（v2.1 P5 参考实现，A 类平台）

LangGraph 是 A 类平台，有原生状态机引擎（StateGraph），
不需要 state_guard.py 包装器，gate 检查由原生 conditional_edge + interrupt_before 实现。

核心设计:
  - FDE Lead = Supervisor 节点，用 function calling 调用 worker
  - Worker agents = 子节点（每个 agent 一个节点）
  - State machine = StateGraph（从 state_machine.json 翻译）
  - Gates = interrupt_before + conditional_edge（原生 HITL）
  - 约束注入 = Command(goto, update) 的 update 字段（原生机制）

依赖:
  pip install langgraph langchain-anthropic langchain-openai

参考:
  - LangGraph Multi-agent Supervisor: https://langchain-ai.github.io/langgraph/tutorials/multi_agent/multi-agent-collaboration/
  - LangGraph Human-in-the-loop: https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/
  - Command(goto, update): https://langchain-ai.github.io/langgraph/concepts/multi_agent/
"""

import json
from pathlib import Path
from typing import Any, Optional

from adapters.base import FileStorage, MessageBus, StateStore, WorkPackageConstraintsMerger


# ============================================================
# 三大接口的 LangGraph 实现
# ============================================================

class LangGraphStorage(FileStorage):
    """LangGraph checkpoint 文件系统存储"""

    def __init__(self, base_dir: str = "./fde_checkpoints"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def read(self, path: str) -> str:
        full = self.base_dir / path
        return full.read_text(encoding="utf-8")

    async def write(self, path: str, content: str) -> None:
        full = self.base_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    async def mkdir(self, parent: str, name: str) -> str:
        full = self.base_dir / parent / name
        full.mkdir(parents=True, exist_ok=True)
        return str(full.relative_to(self.base_dir))

    async def list(self, path: str) -> list:
        full = self.base_dir / path
        if not full.exists():
            return []
        return [f.name for f in full.iterdir()]

    async def exists(self, path: str) -> bool:
        return (self.base_dir / path).exists()


class LangGraphMessageBus(MessageBus):
    """
    LangGraph 消息总线：用 StateGraph state 传递消息。

    在 LangGraph 中，agent 间通信通过共享 state 的 messages 字段，
    不需要独立的消息队列。这里封装为 MessageBus 接口供统一调用。
    """

    def __init__(self):
        self._messages: list = []  # 实际由 StateGraph state 管理，这里是 fallback

    async def send(self, target: str, message: str, msg_type: str = "notification") -> None:
        self._messages.append({
            "to": target,
            "content": message,
            "type": msg_type,
        })

    async def poll(self, filter_dict: Optional[dict] = None) -> list:
        if not filter_dict:
            return list(self._messages)
        result = []
        for msg in self._messages:
            if all(msg.get(k) == v for k, v in filter_dict.items()):
                result.append(msg)
        return result

    async def ack(self, message_id: str) -> None:
        pass  # LangGraph state 传递是同步的，不需要 ack


class LangGraphStateStore(StateStore):
    """
    LangGraph 状态存储：StateGraph state。

    在 LangGraph 中，状态由 checkpointer 持久化（SqliteSaver/PostgresSaver）。
    这里封装为 StateStore 接口，直接操作内存 dict（实际由 checkpointer 持久化）。
    """

    def __init__(self):
        self._store: dict = {}  # {project_id: {key: value}}

    def get(self, project_id: str, key: str) -> Any:
        return self._store.get(project_id, {}).get(key)

    def set(self, project_id: str, key: str, value: Any) -> None:
        self._store.setdefault(project_id, {})[key] = value

    def delete(self, project_id: str, key: str) -> None:
        if project_id in self._store and key in self._store[project_id]:
            del self._store[project_id][key]

    def keys(self, project_id: str) -> list:
        return list(self._store.get(project_id, {}).keys())


# ============================================================
# LangGraph Supervisor（FDE Lead 的 LangGraph 实现）
# ============================================================

def build_supervisor_graph(
    state_machine_path: str,
    worker_agents: dict,
    llm,
    role_cards: dict,
):
    """
    构建 LangGraph Supervisor 图（FDE Lead 作为 supervisor）。

    这是 A 类平台的参考实现，展示如何把 team.yaml + state_machine.json
    翻译为 LangGraph StateGraph。

    Args:
        state_machine_path: state_machine.json 路径
        worker_agents: {agent_id: agent_invoke_fn} 字典
            每个 agent_invoke_fn 接收 (prompt, args) 返回 result
        llm: LLM 实例（支持 function calling，如 ChatAnthropic/ChatOpenAI）
        role_cards: {agent_id: role_card_prompt} 字典

    Returns:
        编译后的 LangGraph 图（compiled StateGraph）

    图结构:
        START -> context -> decide -> act <-> evaluate -> done
                                |       |
                                |       +-> gate_quality (interrupt_before)
                                |       +-> gate_legal (interrupt_before)
                                +-> gate_phase (interrupt_before)

    约束注入:
        通过 Command(goto="worker_node", update={"user_constraints": ...}) 实现
        LangGraph 框架自动把 update 注入到目标节点的 state
    """
    try:
        from langgraph.graph import StateGraph, START, END
        from langgraph.types import Command
        from langgraph.checkpoint.memory import MemorySaver
        from typing_extensions import TypedDict
    except ImportError as e:
        raise ImportError(
            f"LangGraph 未安装，请运行: pip install langgraph\n原错误: {e}"
        )

    # 加载 state_machine.json
    with open(state_machine_path, "r", encoding="utf-8") as f:
        sm = json.load(f)

    # 定义 state schema
    class FDEState(TypedDict, total=False):
        messages: list
        project_id: str
        current_state: str
        work_package: dict
        user_constraints: dict
        execution_plan: dict
        step_results: list
        produced_artifacts: list
        gate_protocol: dict
        qa_verdict: str
        legal_verdict: str
        user_confirmed: bool
        # ... 其他字段

    graph = StateGraph(FDEState)

    # ---------- 节点定义 ----------

    def context_node(state: FDEState) -> Command:
        """第一阶段：上下文收集 - PM-Clarity Clarify"""
        # FDE Lead LLM 重述真问题 + 抽取用户约束
        user_msg = state.get("messages", [])[-1] if state.get("messages") else ""
        # ... LLM 调用逻辑 ...
        # 输出 reframed_problem.json + execution_plan.json (初版)
        return Command(
            goto="decide",
            update={
                "current_state": "decide",
                "produced_artifacts": state.get("produced_artifacts", []) + [
                    "reframed_problem.json", "execution_plan.json"
                ],
            }
        )

    def decide_node(state: FDEState) -> Command:
        """第二阶段：决策 - 输出完整 JSON execution_plan"""
        # FDE Lead LLM 输出 execution_plan
        # 使用 output_enforcement.yaml 的三层降级策略
        # Tier1: with_structured_output (LangGraph 原生)
        return Command(
            goto="act",
            update={
                "current_state": "act",
                "produced_artifacts": state.get("produced_artifacts", []) + ["validated_plan.json"],
            }
        )

    def make_worker_node(agent_id: str, agent_invoke_fn):
        """为 worker agent 创建节点（带约束注入）"""
        def worker_node(state: FDEState) -> Command:
            # v2.1 P3 修正：从 state 读取 user_constraints（不从 LLM 输出读）
            work_package_constraints = state.get("work_package", {}).get("user_constraints", {})

            # 加载 role card
            base_prompt = role_cards.get(agent_id, "")

            # 注入约束块
            constraints_block = WorkPackageConstraintsMerger.format_constraints_block(
                work_package_constraints
            )
            full_prompt = base_prompt + constraints_block

            # 调用子 agent
            result = agent_invoke_fn(full_prompt, state)

            # 收集结果
            step_results = state.get("step_results", [])
            step_results.append({"agent": agent_id, "result": result})

            return Command(
                goto="act",  # 返回 act 继续下一步（act 节点决定下一步）
                update={
                    "step_results": step_results,
                    "produced_artifacts": state.get("produced_artifacts", []) + result.get("artifacts", []),
                }
            )
        return worker_node

    def act_node(state: FDEState) -> Command:
        """第三阶段：执行 - 按计划逐步调用子 agent"""
        plan = state.get("execution_plan", {}).get("plan", [])
        step_results = state.get("step_results", [])

        if len(step_results) < len(plan):
            # 还有步骤未执行，调用下一个 agent
            next_step = plan[len(step_results)]
            next_agent = next_step.get("agent")
            if next_agent and next_agent in worker_agents:
                return Command(goto=f"worker_{next_agent}")
            else:
                raise ValueError(f"未知 agent: {next_agent}")
        else:
            # 所有步骤完成，进 evaluate
            return Command(
                goto="evaluate",
                update={
                    "current_state": "evaluate",
                    "produced_artifacts": state.get("produced_artifacts", []) + ["complete_results.json"],
                }
            )

    def evaluate_node(state: FDEState) -> Command:
        """第四阶段：评估 - 综合结果，QA 审查"""
        # 调用 QA Agent 审查
        # ... 简化：直接通过 ...
        return Command(
            goto="done",
            update={
                "current_state": "done",
                "produced_artifacts": state.get("produced_artifacts", []) + [
                    "evaluation_report.json", "final_delivery.json"
                ],
            }
        )

    def gate_phase_node(state: FDEState) -> Command:
        """Gate: 阶段切换 - 必须 user_confirmed=True 才能转出"""
        if not state.get("user_confirmed"):
            # 阻断，等待用户确认（interrupt_before 会处理）
            return Command(goto="gate_phase")
        return Command(
            goto="context",
            update={
                "current_state": "context",
                "produced_artifacts": state.get("produced_artifacts", []) + ["user_confirmation.json"],
            }
        )

    def gate_quality_node(state: FDEState) -> Command:
        """Gate: 质量门 - 调用 QA Agent"""
        qa_result = worker_agents.get("qa", lambda p, s: {})("", state)
        verdict = qa_result.get("verdict", "fail")
        if verdict == "pass":
            return Command(goto="act", update={"current_state": "act", "qa_verdict": "pass"})
        elif verdict == "rework":
            return Command(goto="act", update={"current_state": "act", "qa_verdict": "rework"})
        else:
            return Command(goto="failed", update={"current_state": "failed"})

    def gate_legal_node(state: FDEState) -> Command:
        """Gate: 法律门 - 调用 Legal Agent"""
        legal_result = worker_agents.get("legal", lambda p, s: {})("", state)
        verdict = legal_result.get("verdict", "fail")
        if verdict == "pass":
            return Command(goto="act", update={"current_state": "act", "legal_verdict": "pass"})
        return Command(goto="act", update={"current_state": "act", "legal_verdict": "needs_human_lawyer"})

    # ---------- 注册节点 ----------
    graph.add_node("context", context_node)
    graph.add_node("decide", decide_node)
    graph.add_node("act", act_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("gate_phase", gate_phase_node)
    graph.add_node("gate_quality", gate_quality_node)
    graph.add_node("gate_legal", gate_legal_node)

    # 为每个 worker agent 注册节点
    for agent_id, invoke_fn in worker_agents.items():
        graph.add_node(f"worker_{agent_id}", make_worker_node(agent_id, invoke_fn))

    # ---------- 边定义 ----------
    graph.add_edge(START, "context")

    # gate 节点用 interrupt_before（原生 HITL）
    # compile 时指定 interrupt_before=["gate_phase", "gate_quality", "gate_legal"]

    # evaluate -> done/failed/gate_phase/act
    # 这些通过节点的 Command(goto=...) 实现，不需要显式 add_edge

    # ---------- 编译 ----------
    checkpointer = MemorySaver()
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["gate_phase", "gate_quality", "gate_legal"],
    )
    return compiled


# ============================================================
# LangGraph 完整适配器
# ============================================================

class LangGraphAdapter:
    """
    LangGraph 平台完整适配器（A 类平台参考实现）。

    与 C 类平台不同，LangGraph 不需要 state_guard.py 包装器：
      - 状态机由 StateGraph 原生引擎管理
      - Gate 由 interrupt_before 原生实现
      - 约束注入由 Command(goto, update) 原生实现

    使用方式:
      from adapters.langgraph.langgraph_adapter import LangGraphAdapter

      adapter = LangGraphAdapter(
          state_machine_path="agents/fde-lead/skills/fde-loop-control/state_machine.json",
          worker_agents={"echo": echo_fn, "delta": delta_fn, ...},
          llm=llm,
          role_cards={"echo": "...", "delta": "...", ...}
      )
      graph = adapter.build_graph()

      # 运行
      result = graph.invoke(
          {"messages": [user_msg], "project_id": "PROJ-001"},
          config={"configurable": {"thread_id": "PROJ-001"}}
      )
    """

    def __init__(
        self,
        state_machine_path: str,
        worker_agents: dict,
        llm,
        role_cards: dict,
    ):
        self.state_machine_path = state_machine_path
        self.worker_agents = worker_agents
        self.llm = llm
        self.role_cards = role_cards
        self.storage = LangGraphStorage()
        self.message_bus = LangGraphMessageBus()
        self.state_store = LangGraphStateStore()

    def build_graph(self):
        """构建并返回编译后的 LangGraph 图"""
        return build_supervisor_graph(
            state_machine_path=self.state_machine_path,
            worker_agents=self.worker_agents,
            llm=self.llm,
            role_cards=self.role_cards,
        )

    def call_worker_agent(
        self,
        tool_name: str,
        tool_args: dict,
        work_package_constraints: dict,
    ):
        """
        LangGraph 中约束注入由 Command(goto, update) 自动完成，
        不需要显式调用 wrapper。这里提供接口兼容性。
        """
        from adapters.base import _extract_agent_id_from_tool_name, call_worker_agent_wrapper

        agent_id = _extract_agent_id_from_tool_name(tool_name)
        invoke_fn = self.worker_agents.get(agent_id)
        if not invoke_fn:
            raise ValueError(f"未知 worker agent: {agent_id}")

        return call_worker_agent_wrapper(
            tool_name=tool_name,
            tool_args=tool_args,
            work_package_constraints=work_package_constraints,
            role_card_loader=lambda aid: self.role_cards.get(aid, ""),
            invoke_sub_agent_fn=invoke_fn,
        )
