"""
FDE Agent Team - R2 反思测试脚本

对 5 个测试用例做结构验证（不需 LLM 调用）：
  T1: 秘书是否真正调度其他 agent（结构层面）
  T2: Plan-then-Execute 是否强制（结构层面）
  T3: 用户约束是否持久化传递（结构层面）
  T4: Gate 是否不可跳过（结构层面，state_guard.py 单元测试）
  T5: 跨平台兼容性（结构层面）

运行:
  python tests/r2_reflection_test.py
"""

import json
import sys
from pathlib import Path

# 把项目根目录加入 sys.path 以便导入 adapters
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

results = []


def check(test_id: str, check_name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    line = f"  {status} {check_name}"
    if detail:
        line += f" - {detail}"
    print(line)
    results.append({"test_id": test_id, "check": check_name, "pass": condition, "detail": detail})


# ============================================================
# T1: 秘书是否真正调度其他 agent
# ============================================================
def test_t1_secretary_dispatches():
    print("\n" + "=" * 60)
    print("T1: 秘书是否真正调度其他 agent（结构验证）")
    print("=" * 60)

    # 1.1 tools.schema.json 中有 8 个 call_* 工具
    tools_path = ROOT / "config" / "tools.schema.json"
    tools = json.loads(tools_path.read_text(encoding="utf-8"))
    worker_tools = tools.get("worker_tools", [])
    tool_names = [t["name"] for t in worker_tools]
    expected_tools = {
        "call_echo_agent", "call_delta_agent", "call_productize_agent",
        "call_research_agent", "call_knowledge_curator", "call_qa_agent",
        "call_legal_agent", "call_coach_agent",
    }
    check("T1", "8 个 call_* 工具已注册", expected_tools.issubset(set(tool_names)),
          f"已有: {sorted(set(tool_names) & expected_tools)}")

    # 1.2 fde-lead SKILL.md 有"禁止自己执行"硬规则
    fde_skill = (ROOT / "agents/fde-lead/SKILL.md").read_text(encoding="utf-8")
    has_no_self_exec = "禁止自己执行" in fde_skill or "禁止" in fde_skill and "call_*" in fde_skill
    check("T1", "FDE Lead 有'禁止自己执行'硬规则", has_no_self_exec)

    # 1.3 fde-lead SKILL.md 要求首次输出 JSON
    has_json_requirement = "execution_plan" in fde_skill and "JSON" in fde_skill
    check("T1", "FDE Lead 要求首次输出 JSON execution_plan", has_json_requirement)

    # 1.4 tools.schema.json 中 call_research_agent 有 user_constraints 参数
    research_tool = next((t for t in worker_tools if t["name"] == "call_research_agent"), None)
    has_uc_param = research_tool and "user_constraints" in research_tool["parameters"]["properties"]
    check("T1", "call_research_agent 有 user_constraints 参数", has_uc_param)

    # 1.5 call_research_agent 要求 user_constraints 为必填
    required_fields = research_tool["parameters"].get("required", []) if research_tool else []
    check("T1", "call_research_agent 把 user_constraints 列为必填", "user_constraints" in required_fields,
          f"required: {required_fields}")


# ============================================================
# T2: Plan-then-Execute 是否强制
# ============================================================
def test_t2_plan_then_execute():
    print("\n" + "=" * 60)
    print("T2: Plan-then-Execute 是否强制（结构验证）")
    print("=" * 60)

    # 2.1 output_enforcement.yaml 存在且有 execution_plan schema
    oe_path = ROOT / "config" / "output_enforcement.yaml"
    check("T2", "output_enforcement.yaml 存在", oe_path.exists())
    if oe_path.exists():
        oe_content = oe_path.read_text(encoding="utf-8")
        check("T2", "有 execution_plan schema", "execution_plan" in oe_content)
        check("T2", "有三层降级策略", "tier1" in oe_content and "tier2" in oe_content and "tier3" in oe_content)
        check("T2", "有 platform_tier_mapping", "platform_tier_mapping" in oe_content)

    # 2.2 tools.schema.json 不再有 response_format（已移到 output_enforcement.yaml）
    tools = json.loads((ROOT / "config/tools.schema.json").read_text(encoding="utf-8"))
    secretary = tools.get("secretary_agent", {})
    has_no_response_format = "response_format" not in secretary
    has_output_enforcement_ref = "output_enforcement" in secretary
    check("T2", "tools.schema.json 移除了 response_format", has_no_response_format)
    check("T2", "tools.schema.json 引用 output_enforcement.yaml", has_output_enforcement_ref)

    # 2.3 state_machine.json 有 context/decide/act/evaluate 四步
    sm = json.loads((ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json").read_text(encoding="utf-8"))
    states = set(sm.get("states", {}).keys())
    four_step = {"context", "decide", "act", "evaluate"}
    check("T2", "state_machine 有四步状态", four_step.issubset(states),
          f"states: {sorted(states & four_step)}")

    # 2.4 context -> decide 需要 execution_plan.json artifact
    context_transitions = sm["states"]["context"]["transitions"]
    context_to_decide = next((t for t in context_transitions if t["to"] == "decide"), None)
    check("T2", "context->decide 需要 execution_plan.json",
          context_to_decide and "execution_plan.json" in context_to_decide.get("artifact_required", ""))


# ============================================================
# T3: 用户约束是否持久化传递
# ============================================================
def test_t3_user_constraints_persist():
    print("\n" + "=" * 60)
    print("T3: 用户约束是否持久化传递（结构验证）")
    print("=" * 60)

    # 3.1 team.yaml 有 constraints_persistence 配置
    team_yaml = (ROOT / "team.yaml").read_text(encoding="utf-8")
    check("T3", "team.yaml 有 constraints_persistence", "constraints_persistence" in team_yaml)

    # 3.2 team.yaml 明确约束注入执行者为 adapter_code_layer
    check("T3", "约束注入执行者 = adapter_code_layer", "adapter_code_layer" in team_yaml)
    check("T3", "injection_trigger = on_call_tool_intercept", "on_call_tool_intercept" in team_yaml)
    check("T3", "failure_mode = fail_closed", "fail_closed" in team_yaml)

    # 3.3 base.py 有 format_constraints_block 函数
    base_py = (ROOT / "adapters/base.py").read_text(encoding="utf-8")
    check("T3", "base.py 有 format_constraints_block", "format_constraints_block" in base_py)
    check("T3", "base.py 有 call_worker_agent_wrapper", "call_worker_agent_wrapper" in base_py)
    check("T3", "约束块包含'[强制用户约束'", "[强制用户约束" in base_py)

    # 3.4 research-agent SKILL.md 有 constraints_followed 输出要求
    research_skill = (ROOT / "agents/research-agent/SKILL.md").read_text(encoding="utf-8")
    check("T3", "research-agent 有 constraints_followed 输出要求", "constraints_followed" in research_skill)

    # 3.5 QA Agent 有 user_constraint_compliance 审查维度
    qa_skill = (ROOT / "agents/qa-agent/SKILL.md").read_text(encoding="utf-8")
    check("T3", "QA Agent 有 user_constraint_compliance 维度", "user_constraint_compliance" in qa_skill)

    # 3.6 team.yaml 中 QA 的 review_dimensions_v21 包含 user_constraint_compliance
    check("T3", "team.yaml QA 有 user_constraint_compliance", "user_constraint_compliance" in team_yaml)

    # 3.7 实际测试约束合并逻辑
    from adapters.base import WorkPackageConstraintsMerger
    wp_constraints = {
        "language": "zh",
        "knowledge_base": "getnote:Q0GpeEvJ",
        "forbidden_sources": ["google_scholar"],
        "min_sources_per_fact": 2,
    }
    llm_passed = {
        "language": "en",  # LLM 试图覆盖，应被忽略
        "custom": ["输出PPT格式"],  # 新增约束，应被接受
    }
    merged = WorkPackageConstraintsMerger.merge_constraints(wp_constraints, llm_passed)
    check("T3", "工作包硬约束不被 LLM 覆盖", merged["language"] == "zh",
          f"merged.language={merged['language']}")
    check("T3", "LLM 新增约束被接受", "输出PPT格式" in merged.get("custom", []),
          f"merged.custom={merged.get('custom')}")

    # 3.8 约束块格式正确
    block = WorkPackageConstraintsMerger.format_constraints_block(merged)
    check("T3", "约束块在末尾有'违反=任务失败'", "违反上述任一约束 = 任务失败" in block)
    check("T3", "约束块包含 knowledge_base", "getnote:Q0GpeEvJ" in block)
    check("T3", "约束块包含 forbidden_sources", "google_scholar" in block)


# ============================================================
# T4: Gate 是否不可跳过
# ============================================================
def test_t4_gate_not_skipped():
    print("\n" + "=" * 60)
    print("T4: Gate 是否不可跳过（结构验证 + state_guard 单元测试）")
    print("=" * 60)

    # 4.1 state_machine.json gate 节点有 blocking: true
    sm = json.loads((ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json").read_text(encoding="utf-8"))
    for gate_name in ["gate_phase", "gate_quality", "gate_legal"]:
        gate = sm["states"].get(gate_name, {})
        check("T4", f"{gate_name} 有 blocking: true", gate.get("blocking") is True)
        check("T4", f"{gate_name} 有 validation 字段", "validation" in gate)
        check("T4", f"{gate_name} 有 on_invalid 字段", "on_invalid" in gate)
        check("T4", f"{gate_name} validation.executor_class 存在", "executor_class" in gate.get("validation", {}))

    # 4.2 anti_skip_enforcement 有 platform_execution_layers
    anti_skip = sm.get("anti_skip_enforcement", {})
    check("T4", "有 anti_skip_enforcement", bool(anti_skip))
    check("T4", "有 platform_execution_layers", "platform_execution_layers" in anti_skip)
    layers = anti_skip.get("platform_execution_layers", {})
    check("T4", "有 A/B/C 三类执行层",
          "A_class_native" in layers and "B_class_conditional" in layers and "C_class_wrapper" in layers)

    # 4.3 state_guard.py 存在且可导入
    check("T4", "state_guard.py 存在", (ROOT / "adapters/state_guard.py").exists())
    try:
        from adapters.state_guard import StateGuard, StateMachineError
        check("T4", "StateGuard 可导入", True)
    except Exception as e:
        check("T4", "StateGuard 可导入", False, str(e))

    # 4.4 state_guard 单元测试：拦截未确认的 gate_phase 跳过
    try:
        from adapters.state_guard import StateGuard, StateMachineError

        class MemStore:
            def __init__(self): self._d = {}
            def get(self, pid, k): return self._d.get(pid, {}).get(k)
            def set(self, pid, k, v): self._d.setdefault(pid, {})[k] = v

        sm_path = str(ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json")
        guard = StateGuard(sm_path, MemStore())
        pid = "R2-T4"

        # 模拟到 gate_phase 的路径
        guard._record_artifact(pid, "execution_plan.json (初版)")
        guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json")
        guard._record_artifact(pid, "validated_plan.json")
        guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
        guard._record_artifact(pid, "phase_transition.json")
        guard._record_artifact(pid, "gate_protocol.json")
        guard.commit_transition(pid, "gate_phase", produced_artifact="phase_transition.json")

        # 未确认就转出应失败
        try:
            guard.commit_transition(pid, "context", user_confirmed=False)
            check("T4", "未确认转出 gate_phase 被拦截", False, "应该抛异常但没抛")
        except StateMachineError as e:
            check("T4", "未确认转出 gate_phase 被拦截", e.violation_type == "skip_without_confirmation",
                  e.violation_type)

        # 确认后转出应成功
        guard.commit_transition(pid, "context", user_confirmed=True)
        check("T4", "确认后转出 gate_phase 成功", guard.get_current_state(pid) == "context")
    except Exception as e:
        check("T4", "state_guard 单元测试运行", False, str(e))


# ============================================================
# T5: 跨平台兼容性
# ============================================================
def test_t5_cross_platform():
    print("\n" + "=" * 60)
    print("T5: 跨平台兼容性（结构验证）")
    print("=" * 60)

    # 5.1 base.py 三大接口存在
    base_py = (ROOT / "adapters/base.py").read_text(encoding="utf-8")
    check("T5", "FileStorage 抽象基类", "class FileStorage" in base_py)
    check("T5", "MessageBus 抽象基类", "class MessageBus" in base_py)
    check("T5", "StateStore 抽象基类", "class StateStore" in base_py)

    # 5.2 飞书适配器存在
    feishu_path = ROOT / "adapters/feishu/feishu_adapter.py"
    check("T5", "飞书适配器文件存在", feishu_path.exists())
    if feishu_path.exists():
        feishu_py = feishu_path.read_text(encoding="utf-8")
        check("T5", "FeishuStorage 类", "class FeishuStorage" in feishu_py)
        check("T5", "FeishuMessageBus 类", "class FeishuMessageBus" in feishu_py)
        check("T5", "FeishuStateStore 类", "class FeishuStateStore" in feishu_py)
        check("T5", "FeishuAdapter 组合类", "class FeishuAdapter" in feishu_py)
        check("T5", "飞书用 lark-cli", "lark-cli" in feishu_py)

    # 5.3 LangGraph 适配器存在
    lg_path = ROOT / "adapters/langgraph/langgraph_adapter.py"
    check("T5", "LangGraph 适配器文件存在", lg_path.exists())
    if lg_path.exists():
        lg_py = lg_path.read_text(encoding="utf-8")
        check("T5", "LangGraphStorage 类", "class LangGraphStorage" in lg_py)
        check("T5", "LangGraphMessageBus 类", "class LangGraphMessageBus" in lg_py)
        check("T5", "LangGraphStateStore 类", "class LangGraphStateStore" in lg_py)
        check("T5", "LangGraphAdapter 组合类", "class LangGraphAdapter" in lg_py)
        check("T5", "LangGraph 用 interrupt_before", "interrupt_before" in lg_py)
        check("T5", "LangGraph 用 Command(goto, update)", "Command" in lg_py)

    # 5.4 state_guard.py 存在（C 类平台用）
    check("T5", "state_guard.py 存在（C 类平台）", (ROOT / "adapters/state_guard.py").exists())

    # 5.5 platform-adapter-spec.md 有约束注入说明
    spec = (ROOT / "docs/platform-adapter-spec.md").read_text(encoding="utf-8")
    check("T5", "platform-adapter-spec 有 3.1 用户约束自动注入", "3.1 用户约束自动注入" in spec)
    check("T5", "spec 有各平台实现方式表", "拦截机制" in spec)

    # 5.6 8 个 worker agent SKILL.md 都是 v2.1
    agents_dir = ROOT / "agents"
    worker_agents = ["echo-agent", "delta-agent", "productize-agent", "research-agent",
                     "knowledge-curator", "qa-agent", "legal-agent", "coach-agent"]
    for agent_name in worker_agents:
        skill = (agents_dir / agent_name / "SKILL.md").read_text(encoding="utf-8")
        check("T5", f"{agent_name} 是 v2.1", 'version: "2.1"' in skill)

    # 5.7 team.yaml 有 platform_compat 配置
    team_yaml = (ROOT / "team.yaml").read_text(encoding="utf-8")
    check("T5", "team.yaml 有 platform_compat", "platform_compat" in team_yaml)
    check("T5", "team.yaml 有 hermes_openclaw 向后兼容", "hermes_openclaw" in team_yaml and "backward_compatible: true" in team_yaml)


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 60)
    print("FDE Agent Team - R2 反思测试（结构验证）")
    print("测试时间:", __import__("datetime").datetime.now().isoformat())
    print("=" * 60)

    test_t1_secretary_dispatches()
    test_t2_plan_then_execute()
    test_t3_user_constraints_persist()
    test_t4_gate_not_skipped()
    test_t5_cross_platform()

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"汇总: {passed}/{total} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        print("\n失败项:")
        for r in results:
            if not r["pass"]:
                print(f"  [FAIL] {r['test_id']} - {r['check']}: {r['detail']}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
