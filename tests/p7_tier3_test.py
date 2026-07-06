"""
P7 测试: Tier3 prompt+parse+retry 降级策略（v2.1）

测试目标:
    验证 adapters/tier3_enforcer.py 的 JSON 解析策略、schema 验证、
    enforce 方法、wrap_llm_call、enforce_with_retry、prompt_suffix 注入、
    事件日志。

覆盖场景:
    1. JSON 解析策略（whole_parse / fenced_json / first_object_extract / json_with_recovery）
    2. JSON 修复（trailing_comma / unclosed_brackets / markdown_fence）
    3. Schema 验证（valid / missing_required / wrong_type / invalid_enum）
    4. enforce 方法（成功 / 解析失败 / schema 失败）
    5. wrap_llm_call（成功 / 失败返回反馈）
    6. enforce_with_retry（第 2 次成功 / 超限）
    7. prompt_suffix 注入（正常 / 不重复）
    8. 事件日志记录
    9. 多平台配置 + 未知平台降级

依据:
    - output_enforcement.yaml Tier3 配置
    - prompt_suffix 放末尾（末尾位置记忆最强）
    - json-repair 库的修复策略参考
"""

import sys
import json
import traceback
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.tier3_enforcer import (
    Tier3Enforcer,
    _try_whole_parse,
    _try_fenced_json,
    _try_first_object_extract,
    _try_json_with_recovery,
    _validate_schema,
    TARGET_SCHEMA,
    PLATFORM_CONFIGS,
)


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

VALID_PLAN = {
    "reframed_problem": "客户需要 AI 转型方案",
    "user_constraints": ["用中文输出", "只搜 arxiv"],
    "plan": [
        {
            "step": 1,
            "agent": "echo",
            "task": "处理客户材料",
            "expected_outputs": ["observation_report.json"],
            "pass_conditions": ["包含≥3个观察点"],
        },
        {
            "step": 2,
            "agent": "research",
            "task": "行业调研",
            "expected_outputs": ["industry_report.md"],
            "pass_conditions": ["每个事实≥2个来源"],
        },
    ],
}


def make_enforcer():
    """创建测试用 Tier3Enforcer"""
    store = MemoryStateStore()
    enforcer = Tier3Enforcer(state_store=store)
    return enforcer, store


# =============================================================================
# 1. JSON 解析策略测试
# =============================================================================

def test_whole_parse_standard():
    """测试 1: whole_parse 标准解析"""
    response = json.dumps(VALID_PLAN)
    data = _try_whole_parse(response)
    assert data is not None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    assert len(data["plan"]) == 2
    print("[OK] 测试1: whole_parse 标准解析")


def test_whole_parse_with_markdown_wrapper():
    """测试 2: whole_parse 能处理整体 markdown 围栏"""
    response = "```json\n" + json.dumps(VALID_PLAN) + "\n```"
    data = _try_whole_parse(response)
    assert data is not None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    print("[OK] 测试2: whole_parse 能处理整体 markdown 围栏")


def test_fenced_json_extract():
    """测试 3: fenced_json 提取围栏块"""
    response = (
        "我先分析了需求，以下是执行计划：\n\n"
        "```json\n"
        + json.dumps(VALID_PLAN)
        + "\n```\n\n"
        "请确认。"
    )
    data = _try_fenced_json(response)
    assert data is not None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    print("[OK] 测试3: fenced_json 提取围栏块")


def test_fenced_json_plain_fence():
    """测试 4: fenced_json 无 json 标记的围栏"""
    response = "```\n" + json.dumps(VALID_PLAN) + "\n```"
    data = _try_fenced_json(response)
    assert data is not None
    assert "plan" in data
    print("[OK] 测试4: fenced_json 无 json 标记的围栏")


def test_first_object_extract_from_text():
    """测试 5: first_object_extract 从自然语言中提取"""
    response = (
        "好的，我来分析。\n\n"
        + json.dumps(VALID_PLAN)
        + "\n\n以上是计划。"
    )
    data = _try_first_object_extract(response)
    assert data is not None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    print("[OK] 测试5: first_object_extract 从自然语言中提取")


def test_first_object_extract_nested():
    """测试 6: first_object_extract 能处理嵌套对象"""
    response = '{"a": {"b": 1}, "c": [1, 2]}'
    data = _try_first_object_extract(response)
    assert data is not None
    assert data["a"]["b"] == 1
    assert data["c"] == [1, 2]
    print("[OK] 测试6: first_object_extract 能处理嵌套对象")


def test_json_recovery_trailing_comma():
    """测试 7: json_with_recovery 修复尾逗号"""
    response = '{"reframed_problem": "test", "user_constraints": ["a",], "plan": [],}'
    data = _try_json_with_recovery(response)
    assert data is not None
    assert data["reframed_problem"] == "test"
    print("[OK] 测试7: json_with_recovery 修复尾逗号")


def test_json_recovery_unclosed_brackets():
    """测试 8: json_with_recovery 补全未闭合括号"""
    response = '{"reframed_problem": "test", "user_constraints": ["a"], "plan": ['
    data = _try_json_with_recovery(response)
    assert data is not None
    assert data["reframed_problem"] == "test"
    assert data["plan"] == []
    print("[OK] 测试8: json_with_recovery 补全未闭合括号")


def test_json_recovery_markdown_fence():
    """测试 9: json_with_recovery 去除 markdown 围栏"""
    response = "```json\n" + json.dumps(VALID_PLAN) + "\n```"
    data = _try_json_with_recovery(response)
    assert data is not None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    print("[OK] 测试9: json_with_recovery 去除 markdown 围栏")


def test_parse_all_strategies_fail():
    """测试 10: 所有策略失败"""
    response = "这是纯文本，没有任何 JSON。"
    enforcer, _ = make_enforcer()
    data, error = enforcer.parse_json(response)
    assert data is None
    assert "失败" in error
    print("[OK] 测试10: 所有策略失败 → 返回错误")


# =============================================================================
# 2. Schema 验证测试
# =============================================================================

def test_schema_valid():
    """测试 11: 合法 schema 验证通过"""
    is_valid, errors = _validate_schema(VALID_PLAN, TARGET_SCHEMA)
    assert is_valid
    assert len(errors) == 0
    print("[OK] 测试11: 合法 schema 验证通过")


def test_schema_missing_required():
    """测试 12: 缺少 required 字段"""
    data = {"reframed_problem": "test"}  # 缺 user_constraints 和 plan
    is_valid, errors = _validate_schema(data, TARGET_SCHEMA)
    assert not is_valid
    assert any("user_constraints" in e for e in errors)
    assert any("plan" in e for e in errors)
    print("[OK] 测试12: 缺少 required 字段被检测")


def test_schema_wrong_type():
    """测试 13: 类型错误"""
    data = {
        "reframed_problem": 123,  # 应该是 string
        "user_constraints": "not_an_array",  # 应该是 array
        "plan": [],
    }
    is_valid, errors = _validate_schema(data, TARGET_SCHEMA)
    assert not is_valid
    assert any("string" in e for e in errors)
    assert any("array" in e for e in errors)
    print("[OK] 测试13: 类型错误被检测")


def test_schema_invalid_enum():
    """测试 14: enum 值非法"""
    data = {
        "reframed_problem": "test",
        "user_constraints": [],
        "current_phase": "invalid_phase",  # 不在 enum 中
        "plan": [
            {
                "step": 1,
                "agent": "invalid_agent",  # 不在 enum 中
                "task": "test",
                "expected_outputs": ["out"],
                "pass_conditions": ["cond"],
            }
        ],
    }
    is_valid, errors = _validate_schema(data, TARGET_SCHEMA)
    assert not is_valid
    assert any("enum" in e.lower() for e in errors)
    print("[OK] 测试14: enum 值非法被检测")


def test_schema_plan_item_missing_required():
    """测试 15: plan 项缺少 required 字段"""
    data = {
        "reframed_problem": "test",
        "user_constraints": [],
        "plan": [
            {"step": 1, "agent": "echo"},  # 缺 task, expected_outputs, pass_conditions
        ],
    }
    is_valid, errors = _validate_schema(data, TARGET_SCHEMA)
    assert not is_valid
    assert any("task" in e for e in errors)
    assert any("expected_outputs" in e for e in errors)
    print("[OK] 测试15: plan 项缺少 required 字段被检测")


# =============================================================================
# 3. enforce 方法测试
# =============================================================================

def test_enforce_success():
    """测试 16: enforce 成功"""
    enforcer, _ = make_enforcer()
    response = json.dumps(VALID_PLAN)
    data, error = enforcer.enforce(response, "hermes_openclaw")
    assert data is not None
    assert error is None
    assert data["reframed_problem"] == "客户需要 AI 转型方案"
    print("[OK] 测试16: enforce 成功")


def test_enforce_parse_fail():
    """测试 17: enforce 解析失败"""
    enforcer, _ = make_enforcer()
    response = "这是纯文本"
    data, error = enforcer.enforce(response, "hermes_openclaw")
    assert data is None
    assert "解析失败" in error
    print("[OK] 测试17: enforce 解析失败 → 返回错误反馈")


def test_enforce_schema_fail():
    """测试 18: enforce schema 验证失败"""
    enforcer, _ = make_enforcer()
    bad_plan = {"reframed_problem": "test"}  # 缺 required 字段
    response = json.dumps(bad_plan)
    data, error = enforcer.enforce(response, "hermes_openclaw")
    assert data is None
    assert "Schema 验证失败" in error
    print("[OK] 测试18: enforce schema 失败 → 返回错误反馈")


def test_enforce_fenced_json_success():
    """测试 19: enforce 从围栏块解析成功"""
    enforcer, _ = make_enforcer()
    response = "计划如下：\n```json\n" + json.dumps(VALID_PLAN) + "\n```"
    data, error = enforcer.enforce(response, "coze")
    assert data is not None
    assert error is None
    print("[OK] 测试19: enforce 从围栏块解析成功（coze 平台）")


# =============================================================================
# 4. wrap_llm_call 测试
# =============================================================================

def test_wrap_llm_call_success():
    """测试 20: wrap_llm_call 合法 JSON 直接通过"""
    enforcer, store = make_enforcer()
    response = json.dumps(VALID_PLAN)

    def mock_llm(messages):
        return response

    wrapped = enforcer.wrap_llm_call(mock_llm, "TEST-P7", "hermes_openclaw")
    result = wrapped([])

    # 验证返回原 response
    assert result == response
    # 验证无 retry 事件
    log = store.get("TEST-P7", "tier3_event_log") or []
    assert len(log) == 0
    print("[OK] 测试20: wrap_llm_call 合法 JSON 直接通过")


def test_wrap_llm_call_fail():
    """测试 21: wrap_llm_call 非法 JSON 返回反馈"""
    enforcer, store = make_enforcer()
    response = "这不是 JSON"

    def mock_llm(messages):
        return response

    wrapped = enforcer.wrap_llm_call(mock_llm, "TEST-P7", "hermes_openclaw")
    result = wrapped([])

    # 验证返回包含错误反馈
    assert "解析失败" in result
    assert result.startswith(response)  # 原 response 在前

    # 验证记录了 parse_retry 事件
    log = store.get("TEST-P7", "tier3_event_log") or []
    assert len(log) >= 1
    assert log[-1]["event_type"] == "parse_retry"

    # 验证 retry count
    count = store.get("TEST-P7", "tier3_retry_count")
    assert count == 1

    print("[OK] 测试21: wrap_llm_call 非法 JSON → 返回反馈 + 记录事件")


def test_wrap_llm_call_should_enforce_fn():
    """测试 22: should_enforce_fn 返回 False 时不强制"""
    enforcer, store = make_enforcer()
    response = "非 JSON 但不需要强制"

    def mock_llm(messages):
        return response

    # should_enforce_fn 返回 False
    wrapped = enforcer.wrap_llm_call(
        mock_llm, "TEST-P7", "hermes_openclaw",
        should_enforce_fn=lambda pid: False,
    )
    result = wrapped([])

    # 验证返回原 response（无反馈）
    assert result == response
    # 验证无事件
    log = store.get("TEST-P7", "tier3_event_log") or []
    assert len(log) == 0
    print("[OK] 测试22: should_enforce_fn=False → 不强制 JSON")


# =============================================================================
# 5. enforce_with_retry 测试
# =============================================================================

def test_enforce_with_retry_success_first_try():
    """测试 23: enforce_with_retry 第一次就成功"""
    enforcer, store = make_enforcer()
    response = json.dumps(VALID_PLAN)

    call_count = [0]

    def mock_llm(messages):
        call_count[0] += 1
        return response

    data, last_response = enforcer.enforce_with_retry(
        mock_llm, [], "TEST-P7", "hermes_openclaw"
    )

    assert data is not None
    assert call_count[0] == 1  # 只调用了 1 次
    print("[OK] 测试23: enforce_with_retry 第一次就成功")


def test_enforce_with_retry_success_second_try():
    """测试 24: enforce_with_retry 第二次成功"""
    enforcer, store = make_enforcer()
    call_count = [0]

    def mock_llm(messages):
        call_count[0] += 1
        if call_count[0] == 1:
            return "第一次输出非 JSON"
        return json.dumps(VALID_PLAN)

    data, last_response = enforcer.enforce_with_retry(
        mock_llm, [], "TEST-P7", "hermes_openclaw"
    )

    assert data is not None
    assert call_count[0] == 2  # 调用了 2 次

    # 验证记录了 parse_retry + retry_succeeded
    log = store.get("TEST-P7", "tier3_event_log") or []
    event_types = [e["event_type"] for e in log]
    assert "parse_retry" in event_types
    assert "retry_succeeded" in event_types

    print("[OK] 测试24: enforce_with_retry 第二次成功")


def test_enforce_with_retry_max_exceeded():
    """测试 25: enforce_with_retry 超过最大重试次数"""
    enforcer, store = make_enforcer()

    def mock_llm(messages):
        return "总是输出非 JSON"

    data, last_response = enforcer.enforce_with_retry(
        mock_llm, [], "TEST-P7", "hermes_openclaw", max_retries=2
    )

    assert data is None  # 失败
    assert "总是输出非 JSON" in last_response

    # 验证记录了 max_retries_exceeded
    log = store.get("TEST-P7", "tier3_event_log") or []
    event_types = [e["event_type"] for e in log]
    assert "max_retries_exceeded" in event_types

    # 验证 retry count = 3（max_retries + 1 次尝试，但只有 max_retries 次是 retry）
    # 第 1 次不是 retry，第 2、3 次是 retry
    count = store.get("TEST-P7", "tier3_retry_count")
    assert count == 2  # 2 次 retry（第 2 和第 3 次尝试）

    print("[OK] 测试25: enforce_with_retry 超限 → 记录 max_retries_exceeded")


# =============================================================================
# 6. prompt_suffix 注入测试
# =============================================================================

def test_inject_prompt_suffix():
    """测试 26: prompt_suffix 注入到末尾"""
    enforcer, _ = make_enforcer()
    system_prompt = "你是 FDE Lead。"
    result = enforcer.inject_prompt_suffix(system_prompt, "coze")

    # 验证原内容在前
    assert result.startswith("你是 FDE Lead。")
    # 验证 suffix 在末尾
    assert "[输出格式硬约束]" in result
    assert result.endswith("违反 = 任务失败。")
    print("[OK] 测试26: prompt_suffix 注入到末尾")


def test_inject_prompt_suffix_no_duplicate():
    """测试 27: 不重复注入"""
    enforcer, _ = make_enforcer()
    system_prompt = "你是 FDE Lead。"
    result1 = enforcer.inject_prompt_suffix(system_prompt, "coze")
    result2 = enforcer.inject_prompt_suffix(result1, "coze")

    # 验证不会重复注入
    assert result1 == result2
    print("[OK] 测试27: 不重复注入")


def test_get_prompt_suffix_unknown_platform():
    """测试 28: 未知平台降级到 hermes_openclaw"""
    enforcer, _ = make_enforcer()
    suffix = enforcer.get_prompt_suffix("unknown_platform")
    # 应该返回 hermes_openclaw 的配置
    assert "首字符必须是 {" in suffix
    print("[OK] 测试28: 未知平台降级到 hermes_openclaw 配置")


# =============================================================================
# 7. 多平台配置测试
# =============================================================================

def test_multi_platform_configs_exist():
    """测试 29: 多平台配置都存在"""
    expected_platforms = ["coze", "hermes_openclaw", "trae_claude_code", "workbuddy", "feishu"]
    for p in expected_platforms:
        assert p in PLATFORM_CONFIGS, f"缺少平台配置: {p}"
        config = PLATFORM_CONFIGS[p]
        assert "prompt_suffix" in config
        assert "parser_strategies" in config
        assert "max_retries" in config
        assert isinstance(config["parser_strategies"], list)
        assert len(config["parser_strategies"]) >= 2
    print("[OK] 测试29: 5 个平台配置都完整")


def test_coze_uses_fenced_json_first():
    """测试 30: coze 首选 fenced_json 策略"""
    strategies = PLATFORM_CONFIGS["coze"]["parser_strategies"]
    assert strategies[0] == "fenced_json"
    print("[OK] 测试30: coze 首选 fenced_json")


def test_trae_uses_whole_parse_first():
    """测试 31: trae 首选 whole_parse 策略"""
    strategies = PLATFORM_CONFIGS["trae_claude_code"]["parser_strategies"]
    assert strategies[0] == "whole_parse"
    print("[OK] 测试31: trae 首选 whole_parse")


# =============================================================================
# 8. 事件日志测试
# =============================================================================

def test_event_log_format():
    """测试 32: 事件日志格式正确"""
    enforcer, store = make_enforcer()
    response = "非 JSON"

    def mock_llm(messages):
        return response

    wrapped = enforcer.wrap_llm_call(mock_llm, "TEST-P7", "coze")
    wrapped([])

    log = store.get("TEST-P7", "tier3_event_log") or []
    assert len(log) >= 1
    entry = log[-1]
    assert "timestamp" in entry
    assert "layer" in entry
    assert entry["layer"] == "tier3"
    assert "event_type" in entry
    assert "detail" in entry
    print("[OK] 测试32: 事件日志格式正确")


# =============================================================================
# 9. 与 StateGuard 组合的兼容性测试
# =============================================================================

def test_compatible_with_state_guard():
    """测试 33: Tier3 可与 StateGuard 链式组合"""
    from adapters.state_guard import StateGuard

    # 创建 StateGuard
    sm_path = PROJECT_ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json"
    guard_store = MemoryStateStore()
    guard = StateGuard(str(sm_path), guard_store)

    # 创建 Tier3Enforcer
    tier3_store = MemoryStateStore()
    enforcer = Tier3Enforcer(state_store=tier3_store)

    # 模拟 LLM 输出：合法 JSON + 状态标签
    response = (
        json.dumps(VALID_PLAN)
        + '\n<state_transition current="context" target="decide" artifact="execution_plan.json (初版)" reason="完成 plan" />'
    )

    def mock_llm(messages):
        return response

    # 链式包装：先 Tier3 再 StateGuard
    # Tier3 检查 JSON 格式，StateGuard 检查状态转换
    tier3_wrapped = enforcer.wrap_llm_call(mock_llm, "TEST-COMBO", "trae_claude_code")
    guard_wrapped = guard.wrap_llm_call(tier3_wrapped, "TEST-COMBO")

    result = guard_wrapped([])

    # 验证 Tier3 通过（返回原 response，无错误反馈）
    assert "Tier3 解析失败" not in result
    # 验证 StateGuard 通过（状态更新为 decide）
    assert guard.get_current_state("TEST-COMBO") == "decide"

    print("[OK] 测试33: Tier3 + StateGuard 链式组合成功")


# =============================================================================
# 主测试入口
# =============================================================================

def run_all_tests():
    """运行所有 P7 测试"""
    tests = [
        # 1. JSON 解析策略
        test_whole_parse_standard,
        test_whole_parse_with_markdown_wrapper,
        test_fenced_json_extract,
        test_fenced_json_plain_fence,
        test_first_object_extract_from_text,
        test_first_object_extract_nested,
        test_json_recovery_trailing_comma,
        test_json_recovery_unclosed_brackets,
        test_json_recovery_markdown_fence,
        test_parse_all_strategies_fail,
        # 2. Schema 验证
        test_schema_valid,
        test_schema_missing_required,
        test_schema_wrong_type,
        test_schema_invalid_enum,
        test_schema_plan_item_missing_required,
        # 3. enforce 方法
        test_enforce_success,
        test_enforce_parse_fail,
        test_enforce_schema_fail,
        test_enforce_fenced_json_success,
        # 4. wrap_llm_call
        test_wrap_llm_call_success,
        test_wrap_llm_call_fail,
        test_wrap_llm_call_should_enforce_fn,
        # 5. enforce_with_retry
        test_enforce_with_retry_success_first_try,
        test_enforce_with_retry_success_second_try,
        test_enforce_with_retry_max_exceeded,
        # 6. prompt_suffix 注入
        test_inject_prompt_suffix,
        test_inject_prompt_suffix_no_duplicate,
        test_get_prompt_suffix_unknown_platform,
        # 7. 多平台配置
        test_multi_platform_configs_exist,
        test_coze_uses_fenced_json_first,
        test_trae_uses_whole_parse_first,
        # 8. 事件日志
        test_event_log_format,
        # 9. StateGuard 组合兼容性
        test_compatible_with_state_guard,
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
    print(f"P7 测试结果: {passed}/{passed+failed} 通过")
    if failures:
        print(f"失败 {failed} 项:")
        for name, err in failures:
            print(f"  - {name}: {err}")
    print(f"{'='*60}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
