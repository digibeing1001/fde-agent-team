"""
P6 测试: 强制状态输出标签协议（v2.1.1）

测试目标:
    验证 state_guard.py 的 XML 标签解析、missing tag 检测、no-change 处理、
    非法转换拦截、JSON 兼容、wrap_llm_call 集成。

覆盖场景:
    1. XML 标签解析（标准/no-change/属性顺序/带前缀）
    2. missing tag 检测（无标签 → retry 反馈）
    3. malformed tag 检测（标签存在但格式错误）
    4. no-change 标签处理（不触发转换）
    5. 非法转换拦截（context → done）
    6. 合法转换成功（context → decide）
    7. gate 未确认拦截（gate_phase → context 未确认）
    8. JSON 解析兼容（state_transition/waiting_for/verdict 三种）
    9. wrap_llm_call 集成测试（missing tag / 合法转换 / 非法转换）

依据:
    - Anthropic XML tags 官方推荐
    - self-correction via feedback 模式
    - fde-lead SKILL.md v2.1.1 强制状态输出标签协议
"""

import sys
import json
import traceback
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.state_guard import StateGuard, StateMachineError


# =============================================================================
# 测试用 MemoryStateStore
# =============================================================================

class MemoryStateStore:
    """简单内存 StateStore 用于测试"""

    def __init__(self):
        self.data = {}

    def get(self, project_id, key):
        return self.data.get(f"{project_id}:{key}")

    def set(self, project_id, key, value):
        self.data[f"{project_id}:{key}"] = value

    def delete(self, project_id, key):
        self.data.pop(f"{project_id}:{key}", None)

    def keys(self, project_id):
        return [k.split(":")[1] for k in self.data if k.startswith(f"{project_id}:")]


# =============================================================================
# 测试辅助
# =============================================================================

def make_guard(project_id="TEST-P6"):
    """创建测试用 StateGuard 实例"""
    store = MemoryStateStore()
    sm_path = PROJECT_ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json"
    guard = StateGuard(str(sm_path), store)
    return guard, store, project_id


def make_guard_with_artifact(project_id="TEST-P6", artifacts=None):
    """创建带预设 artifact 的 StateGuard"""
    guard, store, pid = make_guard(project_id)
    if artifacts:
        for a in artifacts:
            guard._record_artifact(project_id, a)
    return guard, store, pid


# =============================================================================
# 1. XML 标签解析测试
# =============================================================================

def test_xml_tag_parsing_standard():
    """测试 1: 标准 XML 标签解析"""
    guard, _, _ = make_guard()
    response = '''基于用户需求，我重述真问题如下：...

```json
{"reframed_problem": "...", "plan": [...]}
```

<state_transition current="context" target="decide" artifact="execution_plan.json" reason="完成 Clarify" />'''

    transition = guard._extract_transition_from_response(response)

    assert transition is not None, "应该解析到 transition"
    assert transition["target_state"] == "decide"
    assert transition["produced_artifact"] == "execution_plan.json"
    assert transition["current_state"] == "context"
    assert transition["reason"] == "完成 Clarify"
    assert transition["_source"] == "xml"
    print("[OK] 测试1: 标准 XML 标签解析")


def test_xml_tag_no_change():
    """测试 2: no-change 标签解析"""
    guard, _, _ = make_guard()
    response = '等待用户补充材料。\n<state_transition current="context" target="context" artifact="" reason="等待补充" />'

    transition = guard._extract_transition_from_response(response)

    assert transition is not None
    assert transition["target_state"] == "context"
    assert transition["produced_artifact"] is None  # artifact="" → None
    assert transition["current_state"] == "context"
    print("[OK] 测试2: no-change 标签解析")


def test_xml_tag_attribute_order():
    """测试 3: 属性顺序不同也能解析"""
    guard, _, _ = make_guard()
    # reason 在前，target 在后
    response = '<state_transition reason="测试" target="act" current="decide" artifact="validated_plan.json" />'

    transition = guard._extract_transition_from_response(response)

    assert transition is not None
    assert transition["target_state"] == "act"
    assert transition["produced_artifact"] == "validated_plan.json"
    assert transition["current_state"] == "decide"
    assert transition["reason"] == "测试"
    print("[OK] 测试3: 属性顺序不同也能解析")


def test_xml_tag_with_prefix():
    """测试 4: 带自然语言前缀的输出"""
    guard, _, _ = make_guard()
    response = '''我先分析了客户需求，发现关键点是 X。

接下来我打算调用 research_agent 进行行业调研。

<state_transition current="act" target="act" artifact="step_results.json" reason="research_agent 完成" />'''

    transition = guard._extract_transition_from_response(response)

    assert transition is not None
    assert transition["target_state"] == "act"
    assert transition["produced_artifact"] == "step_results.json"
    print("[OK] 测试4: 带自然语言前缀的输出")


# =============================================================================
# 2. missing tag 检测测试
# =============================================================================

def test_missing_tag_detection():
    """测试 5: missing tag 检测 - LLM 输出无标签"""
    guard, store, pid = make_guard()

    # 模拟 LLM 输出无标签
    response = "基于用户需求，我重述真问题如下：... [正文结束，无标签]"

    # 用 wrap_llm_call 包装
    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回包含 missing tag 反馈
    assert "[StateGuard 检测到输出缺少状态标签]" in result
    assert "<state_transition>" in result  # 反馈中包含标签格式说明

    # 验证 violation_log 记录了 missing_state_tag
    log = store.get(pid, "violation_log") or []
    assert len(log) >= 1
    assert log[-1]["violation_type"] == "missing_state_tag"

    # 验证 missing_tag_count 增加
    count = store.get(pid, "missing_tag_count")
    assert count == 1

    # 验证状态没变（仍是初始 context）
    assert guard.get_current_state(pid) == "context"

    print("[OK] 测试5: missing tag 检测 - 触发 retry 反馈 + 记录违规 + 状态不变")


def test_missing_tag_count_increments():
    """测试 6: 多次 missing tag，count 累加"""
    guard, store, pid = make_guard()

    def mock_llm(messages):
        return "无标签输出"

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    wrapped([])
    wrapped([])

    count = store.get(pid, "missing_tag_count")
    assert count == 2
    print("[OK] 测试6: 多次 missing tag，count 累加到 2")


# =============================================================================
# 3. malformed tag 检测测试
# =============================================================================

def test_malformed_tag_no_target():
    """测试 7: 标签存在但无 target 属性"""
    guard, store, pid = make_guard()

    # 标签存在但无 target
    response = '<state_transition current="context" reason="测试" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回包含 malformed 反馈
    assert "[StateGuard 检测到状态标签格式错误]" in result

    # 验证 violation_log 记录了 malformed_state_tag
    log = store.get(pid, "violation_log") or []
    assert any(e["violation_type"] == "malformed_state_tag" for e in log)

    print("[OK] 测试7: 标签存在但无 target → malformed 反馈")


# =============================================================================
# 4. no-change 标签处理测试
# =============================================================================

def test_no_change_no_transition():
    """测试 8: no-change 标签不触发转换"""
    guard, store, pid = make_guard()

    # 当前状态是 context，输出 no-change 标签
    response = '等待用户补充材料。\n<state_transition current="context" target="context" artifact="" reason="等待" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回原 response（无错误反馈）
    assert result == response

    # 验证状态没变
    assert guard.get_current_state(pid) == "context"

    # 验证 violation_log 为空
    log = store.get(pid, "violation_log") or []
    assert len(log) == 0

    print("[OK] 测试8: no-change 标签 → 正常返回，不转换，无违规")


# =============================================================================
# 5. 非法转换拦截测试
# =============================================================================

def test_illegal_transition_blocked():
    """测试 9: 非法转换 context → done 被拦截"""
    guard, store, pid = make_guard()

    # context → done 不在 transitions 中
    response = '<state_transition current="context" target="done" artifact="final.json" reason="想直接结束" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回包含错误反馈
    assert "[StateGuard 拒绝状态转换]" in result
    assert "invalid_transition" in result

    # 验证 violation_log 记录了 invalid_transition
    log = store.get(pid, "violation_log") or []
    assert any(e["violation_type"] == "invalid_transition" for e in log)

    # 验证状态没变
    assert guard.get_current_state(pid) == "context"

    print("[OK] 测试9: 非法转换 context → done 被拦截")


def test_gate_skip_without_confirmation():
    """测试 10: gate_phase 未确认就转出被拦截"""
    guard, store, pid = make_guard()
    # context → decide: 需要 "execution_plan.json (初版)"
    guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json (初版)")
    # decide → act: 需要 "validated_plan.json"
    guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
    # act → gate_phase: 需要 "phase_transition.json" + gate_phase.required_artifacts "gate_protocol.json"
    guard._record_artifact(pid, "gate_protocol.json")
    guard.commit_transition(pid, "gate_phase", produced_artifact="phase_transition.json")
    assert guard.get_current_state(pid) == "gate_phase"

    # 未确认就转出（target=context 但 user_confirmed 未设）
    response = '<state_transition current="gate_phase" target="context" artifact="user_confirmation.json" reason="用户已确认" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证拦截 skip_without_confirmation
    assert "[StateGuard 拒绝状态转换]" in result
    assert "skip_without_confirmation" in result

    # 验证状态仍是 gate_phase
    assert guard.get_current_state(pid) == "gate_phase"

    print("[OK] 测试10: gate_phase 未确认 → skip_without_confirmation 拦截")


# =============================================================================
# 6. 合法转换成功测试
# =============================================================================

def test_legal_transition_success():
    """测试 11: 合法转换 context → decide 成功"""
    guard, store, pid = make_guard()

    # context → decide 需要 artifact "execution_plan.json (初版)"（state_machine.json 定义）
    # transition 里声明了 artifact="execution_plan.json (初版)"，commit_transition 会自动记录
    response = '<state_transition current="context" target="decide" artifact="execution_plan.json (初版)" reason="完成 plan" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回原 response（无错误反馈）
    assert result == response

    # 验证状态更新为 decide
    assert guard.get_current_state(pid) == "decide"

    # 验证 artifact 被记录
    produced = store.get(pid, "produced_artifacts") or []
    assert "execution_plan.json (初版)" in produced

    print("[OK] 测试11: 合法转换 context → decide 成功")


def test_legal_transition_with_user_confirmation():
    """测试 12: gate_phase 用户确认后转出成功"""
    guard, store, pid = make_guard()

    guard.commit_transition(pid, "decide", produced_artifact="execution_plan.json (初版)")
    guard.commit_transition(pid, "act", produced_artifact="validated_plan.json")
    guard._record_artifact(pid, "gate_protocol.json")
    guard.commit_transition(pid, "gate_phase", produced_artifact="phase_transition.json")

    # 用户确认后转出
    response = '<state_transition current="gate_phase" target="context" artifact="user_confirmation.json" reason="用户已确认" />'

    # 需要在 transition 中带 user_confirmed=true
    # XML 标签目前不支持 user_confirmed 属性，需要用 JSON 或特殊处理
    # 这里用 JSON 测试
    json_response = json.dumps({
        "state_transition": {
            "target_state": "context",
            "produced_artifact": "user_confirmation.json",
            "user_confirmed": True
        }
    })

    def mock_llm(messages):
        return json_response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证状态更新为 context
    assert guard.get_current_state(pid) == "context"

    print("[OK] 测试12: gate_phase 用户确认后转出成功（JSON 带 user_confirmed）")


# =============================================================================
# 7. JSON 解析兼容测试
# =============================================================================

def test_json_state_transition_compat():
    """测试 13: JSON state_transition 字段兼容"""
    guard, _, _ = make_guard()
    response = json.dumps({
        "state_transition": {
            "target_state": "decide",
            "produced_artifact": "execution_plan.json"
        }
    })

    transition = guard._extract_transition_from_response(response)

    assert transition is not None
    assert transition["target_state"] == "decide"
    assert transition["produced_artifact"] == "execution_plan.json"
    assert transition["_source"] == "json"
    print("[OK] 测试13: JSON state_transition 字段兼容")


def test_json_gate_compat():
    """测试 14: JSON gate waiting_for 兼容"""
    guard, _, _ = make_guard()
    response = json.dumps({
        "gate_name": "阶段切换",
        "waiting_for": "user_confirmation"
    })

    transition = guard._extract_transition_from_response(response)

    assert transition is not None
    assert transition["target_state"] == "gate_phase"
    assert transition["produced_artifact"] == "gate_protocol.json"
    assert transition["_source"] == "json_gate"
    print("[OK] 测试14: JSON gate waiting_for 兼容")


def test_json_verdict_compat():
    """测试 15: JSON QA verdict 兼容"""
    guard, _, _ = make_guard()

    # pass
    response_pass = json.dumps({"verdict": "pass"})
    t_pass = guard._extract_transition_from_response(response_pass)
    assert t_pass["target_state"] == "act"
    assert t_pass["produced_artifact"] == "qa_pass.json"

    # rework
    response_rework = json.dumps({"verdict": "rework"})
    t_rework = guard._extract_transition_from_response(response_rework)
    assert t_rework["target_state"] == "act"
    assert t_rework["produced_artifact"] == "rework_list.json"

    # fail
    response_fail = json.dumps({"verdict": "fail"})
    t_fail = guard._extract_transition_from_response(response_fail)
    assert t_fail["target_state"] == "failed"
    assert t_fail["produced_artifact"] == "qa_fail.json"

    print("[OK] 测试15: JSON QA verdict 兼容（pass/rework/fail）")


# =============================================================================
# 8. wrap_llm_call 集成测试
# =============================================================================

def test_wrap_llm_call_xml_legal_transition():
    """测试 16: wrap_llm_call XML 标签合法转换"""
    guard, store, pid = make_guard()

    response = '''完成 PM-Clarity Clarify，输出 execution_plan。

<state_transition current="context" target="decide" artifact="execution_plan.json (初版)" reason="完成 plan" />'''

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证返回原 response
    assert result == response

    # 验证状态更新
    assert guard.get_current_state(pid) == "decide"

    print("[OK] 测试16: wrap_llm_call XML 标签合法转换")


def test_wrap_llm_call_xml_no_change():
    """测试 17: wrap_llm_call no-change 标签"""
    guard, store, pid = make_guard()

    response = '等待补充材料。\n<state_transition current="context" target="context" artifact="" reason="等待" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    assert result == response
    assert guard.get_current_state(pid) == "context"

    # 无违规
    log = store.get(pid, "violation_log") or []
    assert len(log) == 0

    print("[OK] 测试17: wrap_llm_call no-change 标签")


def test_wrap_llm_call_xml_missing_artifact():
    """测试 18: wrap_llm_call 转换需要 artifact 但未产出"""
    guard, store, pid = make_guard()

    # context → decide 需要 execution_plan.json，但 artifact 字段为空
    response = '<state_transition current="context" target="decide" artifact="" reason="想直接转" />'

    def mock_llm(messages):
        return response

    wrapped = guard.wrap_llm_call(mock_llm, pid)
    result = wrapped([])

    # 验证拦截 missing_required_artifact
    assert "[StateGuard 拒绝状态转换]" in result
    assert "missing_required_artifact" in result

    # 状态不变
    assert guard.get_current_state(pid) == "context"

    print("[OK] 测试18: wrap_llm_call 缺 artifact 被拦截")


def test_xml_tag_priority_over_json():
    """测试 19: XML 标签优先于 JSON 解析"""
    guard, _, _ = make_guard()

    # 同时包含 XML 标签和 JSON（理论上不会发生，但测试优先级）
    response = '''<state_transition current="context" target="decide" artifact="execution_plan.json" reason="XML" />

{"state_transition": {"target_state": "act", "produced_artifact": "step_results.json"}}'''

    transition = guard._extract_transition_from_response(response)

    # 应该优先解析 XML
    assert transition["_source"] == "xml"
    assert transition["target_state"] == "decide"
    assert transition["produced_artifact"] == "execution_plan.json"

    print("[OK] 测试19: XML 标签优先于 JSON 解析")


# =============================================================================
# 主测试入口
# =============================================================================

def run_all_tests():
    """运行所有 P6 测试"""
    tests = [
        # 1. XML 标签解析
        test_xml_tag_parsing_standard,
        test_xml_tag_no_change,
        test_xml_tag_attribute_order,
        test_xml_tag_with_prefix,
        # 2. missing tag 检测
        test_missing_tag_detection,
        test_missing_tag_count_increments,
        # 3. malformed tag 检测
        test_malformed_tag_no_target,
        # 4. no-change 标签处理
        test_no_change_no_transition,
        # 5. 非法转换拦截
        test_illegal_transition_blocked,
        test_gate_skip_without_confirmation,
        # 6. 合法转换成功
        test_legal_transition_success,
        test_legal_transition_with_user_confirmation,
        # 7. JSON 解析兼容
        test_json_state_transition_compat,
        test_json_gate_compat,
        test_json_verdict_compat,
        # 8. wrap_llm_call 集成
        test_wrap_llm_call_xml_legal_transition,
        test_wrap_llm_call_xml_no_change,
        test_wrap_llm_call_xml_missing_artifact,
        # 9. 优先级
        test_xml_tag_priority_over_json,
    ]

    passed = 0
    failed = 0
    failures = []

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            failures.append((test.__name__, str(e)))
            print(f"[FAIL] {test.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"P6 测试结果: {passed}/{passed+failed} 通过")
    if failures:
        print(f"失败 {failed} 项:")
        for name, err in failures:
            print(f"  - {name}: {err}")
    print(f"{'='*60}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
