"""
FDE Agent Team - R2 端到端模拟测试

模拟 C 类平台（Hermes/OpenClaw/Trae/飞书）的运行场景，
验证 v2.1 机制在实际 LLM 调用中是否能正确工作。

测试场景:
  E1: FDE Lead 试图跳过 plan 直接执行任务 → state_guard 应拦截
  E2: FDE Lead 试图跳过 gate_phase 不等用户确认 → state_guard 应拦截
  E3: 用户约束通过 call_worker_agent_wrapper 注入到子 agent prompt → 验证
  E4: LLM 试图覆盖工作包硬约束 → 合并逻辑应拒绝覆盖
  E5: 子 agent 输出不含 constraints_followed → 应被标记为不合规

运行:
  python tests/r2_e2e_simulation_test.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"

results = []


def check(test_id, name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status} {name}" + (f" - {detail}" if detail else ""))
    results.append({"test_id": test_id, "check": name, "pass": condition, "detail": detail})


# ============================================================
# E1: FDE Lead 试图跳过 plan 直接执行任务
# ============================================================
def test_e1_skip_plan_blocked():
    print("\n" + "=" * 60)
    print("E1: FDE Lead 试图跳过 plan 直接执行任务")
    print("=" * 60)

    from adapters.state_guard import StateGuard, StateMachineError

    class MemStore:
        def __init__(self): self._d = {}
        def get(self, pid, k): return self._d.get(pid, {}).get(k)
        def set(self, pid, k, v): self._d.setdefault(pid, {})[k] = v

    sm_path = str(ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json")
    guard = StateGuard(sm_path, MemStore())
    pid = "E1"

    # 当前状态是 context，试图直接跳到 act（跳过 decide）
    # context 的 transitions 只允许 -> decide
    try:
        guard.commit_transition(pid, "act")
        check("E1", "跳过 decide 直接进 act 被拦截", False, "应该抛异常")
    except StateMachineError as e:
        check("E1", "跳过 decide 直接进 act 被拦截",
              e.violation_type == "invalid_transition",
              e.violation_type)

    # 试图跳到 evaluate（跳过 decide + act）
    try:
        guard.commit_transition(pid, "evaluate")
        check("E1", "跳过 decide+act 直接进 evaluate 被拦截", False)
    except StateMachineError as e:
        check("E1", "跳过 decide+act 直接进 evaluate 被拦截",
              e.violation_type == "invalid_transition")

    # 试图直接到 done
    try:
        guard.commit_transition(pid, "done")
        check("E1", "跳过所有步骤直接进 done 被拦截", False)
    except StateMachineError as e:
        check("E1", "跳过所有步骤直接进 done 被拦截",
              e.violation_type == "invalid_transition")


# ============================================================
# E2: FDE Lead 试图跳过 gate_phase 不等用户确认
# ============================================================
def test_e2_skip_gate_blocked():
    print("\n" + "=" * 60)
    print("E2: FDE Lead 试图跳过 gate_phase 不等用户确认")
    print("=" * 60)

    from adapters.state_guard import StateGuard, StateMachineError

    class MemStore:
        def __init__(self): self._d = {}
        def get(self, pid, k): return self._d.get(pid, {}).get(k)
        def set(self, pid, k, v): self._d.setdefault(pid, {})[k] = v

    sm_path = str(ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json")
    guard = StateGuard(sm_path, MemStore())
    pid = "E2"

    # 走到 gate_phase
    guard._record_artifact(pid, "execution_plan.json (初版)")
    guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json")
    guard._record_artifact(pid, "validated_plan.json")
    guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
    guard._record_artifact(pid, "phase_transition.json")
    guard._record_artifact(pid, "gate_protocol.json")
    guard.commit_transition(pid, "gate_phase", produced_artifact="phase_transition.json")

    # 试图不等确认直接转出到 context（gate_phase 只允许转出到 context 或 aborted）
    try:
        guard.commit_transition(pid, "context", user_confirmed=False)
        check("E2", "未确认跳到 context 被拦截", False)
    except StateMachineError as e:
        check("E2", "未确认跳到 context 被拦截",
              e.violation_type == "skip_without_confirmation",
              e.violation_type)

    # gate_phase 不允许直接到 act/evaluate/done（不在 transitions 中）
    for target in ["act", "evaluate", "done"]:
        try:
            guard.commit_transition(pid, target, user_confirmed=False)
            check("E2", f"gate_phase -> {target} 被拦截（非法转换）", False)
        except StateMachineError as e:
            check("E2", f"gate_phase -> {target} 被拦截（非法转换）",
                  e.violation_type == "invalid_transition",
                  e.violation_type)

    # 试图跳到 aborted（这是允许的，用户可以中止，但需要 abort_reason.json）
    guard._record_artifact(pid, "abort_reason.json")
    try:
        guard.commit_transition(pid, "aborted", produced_artifact="abort_reason.json")
        check("E2", "跳到 aborted 允许（用户中止，含 abort_reason）", True)
    except StateMachineError as e:
        check("E2", "跳到 aborted 允许（用户中止，含 abort_reason）", False, str(e))


# ============================================================
# E3: 用户约束通过 call_worker_agent_wrapper 注入到子 agent prompt
# ============================================================
def test_e3_constraints_injected():
    print("\n" + "=" * 60)
    print("E3: 用户约束注入到子 agent system prompt")
    print("=" * 60)

    from adapters.base import call_worker_agent_wrapper

    # 模拟工作包约束（用户指定的硬约束）
    work_package_constraints = {
        "language": "zh",
        "knowledge_base": "getnote:Q0GpeEvJ",
        "sources": ["arxiv", "getnote"],
        "forbidden_sources": ["google_scholar"],
        "date_range": "2024-01-01..now",
        "min_sources_per_fact": 2,
    }

    # 模拟 LLM 传递的 tool_args（LLM 可能漏传或改写约束）
    tool_args = {
        "task_id": "T-001",
        "task_description": "调研 LLM Agent 在科研领域的进展",
        "research_scope": "tech_trend",
        "user_constraints": {},  # LLM 没传约束（模拟丢失场景）
    }

    # 模拟 role card loader
    role_card = "# Research Agent\n你是调研专家..."
    role_card_loader = lambda aid: role_card if aid == "research" else ""

    # 捕获实际传给子 agent 的 prompt
    captured_prompt = []
    def mock_invoke(prompt, args):
        captured_prompt.append(prompt)
        return {"sources": [], "key_findings": [], "constraints_followed": []}

    # 执行
    result = call_worker_agent_wrapper(
        tool_name="call_research_agent",
        tool_args=tool_args,
        work_package_constraints=work_package_constraints,
        role_card_loader=role_card_loader,
        invoke_sub_agent_fn=mock_invoke,
    )

    # 验证 prompt 末尾有约束块
    actual_prompt = captured_prompt[0]
    check("E3", "prompt 包含 role card", "# Research Agent" in actual_prompt)
    check("E3", "prompt 末尾有约束块", "[强制用户约束 - 不可违反]" in actual_prompt)
    check("E3", "prompt 包含 knowledge_base 约束", "getnote:Q0GpeEvJ" in actual_prompt)
    check("E3", "prompt 包含 forbidden_sources 约束", "google_scholar" in actual_prompt)
    check("E3", "prompt 包含 min_sources_per_fact 约束", "2 个独立来源" in actual_prompt)
    check("E3", "prompt 包含 language 约束", "zh" in actual_prompt)
    check("E3", "约束块在 prompt 末尾", actual_prompt.rstrip().endswith("违反上述任一约束 = 任务失败"))
    check("E3", "结果含 constraints_injected 标记", "constraints_injected" in result)


# ============================================================
# E4: LLM 试图覆盖工作包硬约束
# ============================================================
def test_e4_llm_cannot_override_hard_constraints():
    print("\n" + "=" * 60)
    print("E4: LLM 试图覆盖工作包硬约束")
    print("=" * 60)

    from adapters.base import WorkPackageConstraintsMerger

    # 工作包硬约束
    wp = {
        "language": "zh",
        "knowledge_base": "getnote:Q0GpeEvJ",
        "min_sources_per_fact": 2,
    }

    # LLM 试图改写（模拟 LLM 不听话的场景）
    llm_passed = {
        "language": "en",  # 试图改成英文
        "knowledge_base": "google",  # 试图改成 google
        "min_sources_per_fact": 1,  # 试图降低标准
        "custom": ["额外要求"],  # 新增约束（应被接受）
    }

    merged = WorkPackageConstraintsMerger.merge_constraints(wp, llm_passed)

    check("E4", "language 不被覆盖（仍为 zh）", merged["language"] == "zh")
    check("E4", "knowledge_base 不被覆盖（仍为 getnote）", merged["knowledge_base"] == "getnote:Q0GpeEvJ")
    check("E4", "min_sources_per_fact 不被降低（仍为 2）", merged["min_sources_per_fact"] == 2)
    check("E4", "LLM 新增的 custom 被接受", "额外要求" in merged.get("custom", []))


# ============================================================
# E5: 子 agent 输出不含 constraints_followed
# ============================================================
def test_e5_missing_constraints_followed_detected():
    print("\n" + "=" * 60)
    print("E5: 子 agent 输出不含 constraints_followed 应被标记")
    print("=" * 60)

    from adapters.base import call_worker_agent_wrapper

    # 模拟子 agent 返回不含 constraints_followed（模拟子 agent 不遵守输出规范）
    def mock_invoke_no_cf(prompt, args):
        return {"sources": [], "key_findings": []}  # 没有 constraints_followed

    result = call_worker_agent_wrapper(
        tool_name="call_research_agent",
        tool_args={"task_id": "T-005", "task_description": "测试", "research_scope": "industry"},
        work_package_constraints={"language": "zh"},
        role_card_loader=lambda aid: "# Research Agent",
        invoke_sub_agent_fn=mock_invoke_no_cf,
    )

    # wrapper 应该自动添加 constraints_injected 标记（供 QA 审查）
    check("E5", "wrapper 自动添加 constraints_injected", "constraints_injected" in result)
    check("E5", "constraints_injected 列出注入的约束", "language" in result.get("constraints_injected", []))

    # QA Agent 可以通过对比 constraints_injected 和 constraints_followed 判断是否合规
    # 如果 constraints_followed 缺失或与 constraints_injected 不一致 = 不合规
    injected = set(result.get("constraints_injected", []))
    followed = set(result.get("constraints_followed", []))
    is_compliant = followed and injected.issubset(followed)
    check("E5", "QA 可检测到不合规（constraints_followed 缺失）", not is_compliant,
          f"injected={injected}, followed={followed}")


# ============================================================
# E6: 完整流程模拟（context → decide → act → gate_phase → 确认 → context）
# ============================================================
def test_e6_full_flow_simulation():
    print("\n" + "=" * 60)
    print("E6: 完整流程模拟")
    print("=" * 60)

    from adapters.state_guard import StateGuard, StateMachineError

    class MemStore:
        def __init__(self): self._d = {}
        def get(self, pid, k): return self._d.get(pid, {}).get(k)
        def set(self, pid, k, v): self._d.setdefault(pid, {})[k] = v

    sm_path = str(ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json")
    guard = StateGuard(sm_path, MemStore())
    pid = "E6"

    # Step 1: context -> decide（产出 reframed_problem + execution_plan 初版）
    check("E6", "初始状态 = context", guard.get_current_state(pid) == "context")
    guard._record_artifact(pid, "execution_plan.json (初版)")
    guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json")
    check("E6", "context -> decide 成功", guard.get_current_state(pid) == "decide")

    # Step 2: decide -> act（产出 validated_plan）
    guard._record_artifact(pid, "validated_plan.json")
    guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
    check("E6", "decide -> act 成功", guard.get_current_state(pid) == "act")

    # Step 3: act -> gate_phase（产出 phase_transition + gate_protocol）
    guard._record_artifact(pid, "phase_transition.json")
    guard._record_artifact(pid, "gate_protocol.json")
    guard.commit_transition(pid, "gate_phase", produced_artifact="phase_transition.json")
    check("E6", "act -> gate_phase 成功", guard.get_current_state(pid) == "gate_phase")

    # Step 4: gate_phase 等待用户确认
    try:
        guard.commit_transition(pid, "context", user_confirmed=False)
        check("E6", "未确认时阻断", False)
    except StateMachineError:
        check("E6", "未确认时阻断", True)

    # Step 5: 用户确认后 -> context（进入下一阶段）
    guard.commit_transition(pid, "context", user_confirmed=True)
    check("E6", "确认后 gate_phase -> context 成功", guard.get_current_state(pid) == "context")

    # Step 6: 第二轮 context -> decide -> act -> evaluate -> done
    guard._record_artifact(pid, "execution_plan.json (初版)")
    guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json")
    guard._record_artifact(pid, "validated_plan.json")
    guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
    guard._record_artifact(pid, "complete_results.json")
    guard.commit_transition(pid, "evaluate", produced_artifact="complete_results.json")
    guard._record_artifact(pid, "evaluation_report.json")
    guard._record_artifact(pid, "final_delivery.json")
    guard.commit_transition(pid, "done", produced_artifact="final_delivery.json")
    check("E6", "完整流程到 done 成功", guard.get_current_state(pid) == "done")

    # done 是终态，不能再转换
    try:
        guard.commit_transition(pid, "context")
        check("E6", "done 终态不可转出", False)
    except StateMachineError as e:
        check("E6", "done 终态不可转出", e.violation_type == "terminal_state_reached")


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 60)
    print("FDE Agent Team - R2 端到端模拟测试")
    print("测试时间:", __import__("datetime").datetime.now().isoformat())
    print("=" * 60)

    test_e1_skip_plan_blocked()
    test_e2_skip_gate_blocked()
    test_e3_constraints_injected()
    test_e4_llm_cannot_override_hard_constraints()
    test_e5_missing_constraints_followed_detected()
    test_e6_full_flow_simulation()

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
