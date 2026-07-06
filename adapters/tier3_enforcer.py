"""
FDE Agent Team - Tier3 Output Enforcer (v2.1 P7 实现)

目的:
    C 类平台（Coze/Trae/WorkBuddy/飞书/Hermes）的 LLM 输出是自由文本，
    没有 Tier1（模型 API response_format）和 Tier2（框架 with_structured_output）
    的原生 JSON 强制能力。Tier3 通过 prompt_suffix + JSON 解析 + schema 验证 +
    失败 retry 实现 JSON 输出强制。

    与 state_guard.py 的关系:
    - state_guard 管状态转换（XML <state_transition> 标签）
    - tier3_enforcer 管输出格式（JSON execution_plan）
    - 两者互补，可链式组合使用

适用平台:
    - Coze (prompt_with_fenced_block)
    - Hermes/OpenClaw (prompt_with_json_instruction)
    - Trae/Claude Code (skill_with_structured_expectation)
    - WorkBuddy (prompt_with_json_instruction)
    - 飞书 (prompt_with_json_instruction)

使用方式:
    from adapters.tier3_enforcer import Tier3Enforcer

    enforcer = Tier3Enforcer(state_store)
    # 1. 手动调用
    data, error = enforcer.enforce(response, "coze")
    # 2. 包装 LLM 调用
    wrapped = enforcer.wrap_llm_call(llm_fn, project_id, "coze")
    # 3. 自动 retry
    data, response = enforcer.enforce_with_retry(llm_fn, messages, project_id, "coze")

设计原则:
    - 零外部依赖（不依赖 PyYAML/jsonschema，自行实现解析和验证）
    - 多策略降级解析（whole_parse → fenced_json → first_object_extract → json_with_recovery）
    - fail_closed: 解析失败 = 返回错误反馈，让 LLM 重试
    - 可观察: 所有解析/验证事件记录到 StateStore 供 Coach Agent 审计
"""

import json
import re
import time
from typing import Any, Callable, Optional, Tuple


# =============================================================================
# 平台配置（从 output_enforcement.yaml Tier3 部分提取）
# =============================================================================

PLATFORM_CONFIGS = {
    "coze": {
        "prompt_suffix": (
            "---\n"
            "[输出格式硬约束]\n"
            "你的回复必须是一个合法 JSON，符合以下结构：\n"
            "```\n"
            '{"reframed_problem": "...", "user_constraints": [...], "plan": [...]}\n'
            "```\n"
            "禁止在 JSON 块外输出任何文字。违反 = 任务失败。"
        ),
        "parser_strategies": ["fenced_json", "first_object_extract", "json_with_recovery"],
        "max_retries": 2,
    },
    "hermes_openclaw": {
        "prompt_suffix": (
            "---\n"
            "[输出格式硬约束]\n"
            "回复必须是合法 JSON execution_plan，结构见上。\n"
            "不要输出任何说明文字、不要输出 markdown 标题、不要前后空行。\n"
            "首字符必须是 {，末字符必须是 }。"
        ),
        "parser_strategies": ["whole_parse", "first_object_extract", "json_with_recovery"],
        "max_retries": 2,
    },
    "trae_claude_code": {
        "prompt_suffix": (
            "输出 execution_plan JSON，结构见 config/tools.schema.json。\n"
            "首字符必须是 {，末字符必须是 }。"
        ),
        # 修复: 加 first_object_extract 兜底
        # 当 LLM 输出 JSON + XML 状态标签混合文本时，whole_parse 整体解析失败，
        # first_object_extract 能用平衡括号匹配提取首个 JSON 对象
        "parser_strategies": ["whole_parse", "fenced_json", "first_object_extract", "json_with_recovery"],
        "max_retries": 3,
    },
    "workbuddy": {
        "prompt_suffix": (
            "---\n"
            "[输出格式硬约束]\n"
            "回复必须是合法 JSON execution_plan。首字符必须是 {。"
        ),
        "parser_strategies": ["whole_parse", "first_object_extract", "json_with_recovery"],
        "max_retries": 2,
    },
    "feishu": {
        "prompt_suffix": (
            "---\n"
            "[输出格式硬约束]\n"
            "回复必须是合法 JSON execution_plan。首字符必须是 {。"
        ),
        "parser_strategies": ["whole_parse", "first_object_extract", "json_with_recovery"],
        "max_retries": 2,
    },
}


# =============================================================================
# target_schema（从 output_enforcement.yaml 提取）
# =============================================================================

TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "reframed_problem": {"type": "string"},
        "user_constraints": {
            "type": "array",
            "items": {"type": "string"},
        },
        "current_phase": {
            "type": "string",
            "enum": ["presales", "research", "implementation", "delivery", "continuous"],
        },
        "current_gate": {"type": "string"},
        "plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "agent": {
                        "type": "string",
                        "enum": ["echo", "delta", "productize", "research",
                                 "knowledge-curator", "qa", "legal", "coach"],
                    },
                    "task": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                    "user_constraints_to_pass": {"type": "array", "items": {"type": "string"}},
                    "expected_outputs": {"type": "array", "items": {"type": "string"}},
                    "pass_conditions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["step", "agent", "task", "expected_outputs", "pass_conditions"],
            },
        },
    },
    "required": ["reframed_problem", "user_constraints", "plan"],
}


# =============================================================================
# JSON 解析策略
# =============================================================================

def _try_whole_parse(response: str) -> Optional[dict]:
    """策略 1: 直接整体解析 json.loads(response)"""
    try:
        text = response.strip()
        # 去除可能的 markdown 围栏（整体包裹的情况）
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*\n', '', text)
            text = re.sub(r'\n```\s*$', '', text)
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _try_fenced_json(response: str) -> Optional[dict]:
    """策略 2: 提取 ```json ... ``` 围栏块"""
    pattern = r'```json\s*\n([\s\S]*?)\n```'
    match = re.search(pattern, response)
    if not match:
        # 尝试无 json 标记的围栏
        pattern = r'```\s*\n([\s\S]*?)\n```'
        match = re.search(pattern, response)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _try_first_object_extract(response: str) -> Optional[dict]:
    """策略 3: 平衡括号匹配提取第一个 {...} 对象"""
    start = response.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(response)):
        c = response[i]

        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(response[start:i + 1])
                    return data if isinstance(data, dict) else None
                except (json.JSONDecodeError, ValueError):
                    return None

    return None


def _try_json_with_recovery(response: str) -> Optional[dict]:
    """
    策略 4: JSON 修复解析
    依次尝试: strip_markdown_fence → fix_trailing_comma → close_unclosed_brackets
    """
    text = response.strip()

    # 1. strip_markdown_fence
    text = re.sub(r'^```(?:json)?\s*\n', '', text)
    text = re.sub(r'\n```\s*$', '', text)

    # 2. fix_trailing_comma（去除 } 和 ] 前的逗号）
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    # 3. close_unclosed_brackets（补全未闭合的括号）
    # 用栈记录开括号顺序，按逆序补全（先补内层 ]，再补外层 }）
    # 修复: 之前用 open_braces/open_brackets 两个计数器，补全时先 } 后 ]，
    #       导致 [...} 顺序错误。正确做法是用栈按开括号顺序逆序补全。
    stack = []  # 记录待闭合的括号，如 ['}', ']'] 表示先开 { 后开 [
    in_string = False
    escape = False

    for c in text:
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            stack.append('}')
        elif c == '[':
            stack.append(']')
        elif c in ('}', ']'):
            # 匹配到闭合括号，弹出栈顶（如果匹配）
            if stack and stack[-1] == c:
                stack.pop()
            # 不匹配的闭合括号忽略（容错）

    # 按栈逆序补全（栈顶是最后开的，最先补）
    if stack:
        text = text.rstrip()
        text += ''.join(reversed(stack))

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


# 策略名 → 函数映射
_PARSER_STRATEGIES = {
    "whole_parse": _try_whole_parse,
    "fenced_json": _try_fenced_json,
    "first_object_extract": _try_first_object_extract,
    "json_with_recovery": _try_json_with_recovery,
}


# =============================================================================
# Schema 验证（简化版，不依赖 jsonschema 库）
# =============================================================================

def _validate_schema(data: Any, schema: dict, path: str = "") -> Tuple[bool, list]:
    """
    验证 data 是否符合 schema（简化版）。

    支持: type / required / properties / items / enum

    Returns:
        (is_valid, errors)
    """
    errors = []
    _validate_node(data, schema, path, errors)
    return len(errors) == 0, errors


def _validate_node(data: Any, schema: dict, path: str, errors: list):
    """递归验证 schema 节点"""
    if "type" not in schema:
        return

    expected_type = schema["type"]

    if expected_type == "object":
        if not isinstance(data, dict):
            errors.append(f"{path or 'root'}: 期望 object，实际 {type(data).__name__}")
            return
        # 检查 required
        for field in schema.get("required", []):
            if field not in data:
                errors.append(f"{path}.{field}: 缺少 required 字段")
        # 递归检查 properties
        for field, field_schema in schema.get("properties", {}).items():
            if field in data:
                _validate_node(data[field], field_schema, f"{path}.{field}", errors)

    elif expected_type == "array":
        if not isinstance(data, list):
            errors.append(f"{path or 'root'}: 期望 array，实际 {type(data).__name__}")
            return
        item_schema = schema.get("items", {})
        if item_schema:
            for i, item in enumerate(data):
                _validate_node(item, item_schema, f"{path}[{i}]", errors)

    elif expected_type == "string":
        if not isinstance(data, str):
            errors.append(f"{path or 'root'}: 期望 string，实际 {type(data).__name__}")

    elif expected_type == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            errors.append(f"{path or 'root'}: 期望 integer，实际 {type(data).__name__}")

    # 检查 enum
    if "enum" in schema and data is not None:
        if data not in schema["enum"]:
            errors.append(f"{path or 'root'}: 值 {repr(data)} 不在 enum {schema['enum']} 中")


# =============================================================================
# Tier3Enforcer 主类
# =============================================================================

class Tier3Enforcer:
    """
    Tier3 输出强制器：prompt_suffix + JSON 解析 + schema 验证 + retry。

    用于 C 类平台（Coze/Trae/WorkBuddy/飞书/Hermes），
    在 LLM 输出 execution_plan 时强制 JSON 格式。

    核心流程:
        1. 注入 prompt_suffix 到 system prompt 末尾（提示 LLM 输出 JSON）
        2. LLM 输出后，按策略顺序尝试解析 JSON
        3. 解析成功后验证 schema
        4. 解析/验证失败 → 构造错误反馈 → LLM retry
        5. 所有事件记录到 StateStore
    """

    def __init__(self, state_store=None, target_schema: dict = None):
        """
        Args:
            state_store: StateStore 接口实现（用于持久化事件日志）
            target_schema: JSON schema（默认使用 TARGET_SCHEMA）
        """
        self.state_store = state_store
        self.target_schema = target_schema or TARGET_SCHEMA

    # =========================================================================
    # prompt_suffix 注入
    # =========================================================================

    def get_prompt_suffix(self, platform: str) -> str:
        """获取平台对应的 prompt_suffix 文本"""
        config = PLATFORM_CONFIGS.get(platform)
        if not config:
            # 未知平台用 hermes_openclaw 配置（最通用）
            config = PLATFORM_CONFIGS["hermes_openclaw"]
        return config["prompt_suffix"]

    def inject_prompt_suffix(self, system_prompt: str, platform: str) -> str:
        """
        将 prompt_suffix 追加到 system prompt 末尾。

        依据：长上下文中末尾位置记忆最强，prompt_suffix 放末尾。
        """
        suffix = self.get_prompt_suffix(platform)
        if suffix in system_prompt:
            return system_prompt  # 避免重复注入
        return system_prompt.rstrip() + "\n\n" + suffix

    # =========================================================================
    # JSON 解析
    # =========================================================================

    def parse_json(self, response: str, strategies: list = None) -> Tuple[Optional[dict], Optional[str]]:
        """
        按策略顺序尝试解析 JSON。

        Args:
            response: LLM 输出文本
            strategies: 策略名列表（默认用 ["whole_parse", "fenced_json",
                        "first_object_extract", "json_with_recovery"]）

        Returns:
            (parsed_data, error_message)
            - 成功: (dict, None)
            - 失败: (None, error_str)
        """
        if strategies is None:
            strategies = ["whole_parse", "fenced_json", "first_object_extract", "json_with_recovery"]

        for strategy_name in strategies:
            strategy_fn = _PARSER_STRATEGIES.get(strategy_name)
            if not strategy_fn:
                continue

            data = strategy_fn(response)
            if data is not None:
                return data, None

        return None, "所有解析策略失败"

    # =========================================================================
    # Schema 验证
    # =========================================================================

    def validate_schema(self, data: dict) -> Tuple[bool, list]:
        """
        验证 data 是否符合 target_schema。

        Returns:
            (is_valid, errors)
        """
        return _validate_schema(data, self.target_schema)

    # =========================================================================
    # enforce: 解析 + 验证（一步到位）
    # =========================================================================

    def enforce(self, response: str, platform: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        解析 + 验证 LLM 输出。

        Args:
            response: LLM 输出文本
            platform: 平台名（决定使用哪些解析策略）

        Returns:
            (parsed_data, error_feedback)
            - 成功: (dict, None)
            - 失败: (None, error_feedback_str)  -- error_feedback 可直接作为 retry 提示
        """
        config = PLATFORM_CONFIGS.get(platform, PLATFORM_CONFIGS["hermes_openclaw"])
        strategies = config["parser_strategies"]

        # 1. 解析 JSON
        data, parse_error = self.parse_json(response, strategies)
        if data is None:
            feedback = self._build_parse_error_feedback(parse_error, response, platform)
            return None, feedback

        # 2. 验证 schema
        is_valid, errors = self.validate_schema(data)
        if not is_valid:
            feedback = self._build_schema_error_feedback(errors, platform)
            return None, feedback

        return data, None

    # =========================================================================
    # wrap_llm_call: 包装 LLM 调用
    # =========================================================================

    def wrap_llm_call(
        self,
        llm_invoke_fn: Callable,
        project_id: str,
        platform: str,
        should_enforce_fn: Optional[Callable[[str], bool]] = None,
    ):
        """
        包装 LLM 调用函数，自动强制 JSON 输出。

        Args:
            llm_invoke_fn: LLM 调用函数 (messages: list) -> response: str
            project_id: 项目 ID
            platform: 平台名
            should_enforce_fn: 判断是否需要强制 JSON 的函数 (project_id) -> bool
                               不传则总是强制

        Returns:
            包装后的 LLM 调用函数
        """

        def wrapped(messages: list) -> str:
            response = llm_invoke_fn(messages)

            # 判断是否需要强制
            if should_enforce_fn and not should_enforce_fn(project_id):
                return response

            data, error = self.enforce(response, platform)
            if data is not None:
                # 解析+验证成功，返回原 response
                return response

            # 失败，记录事件并返回错误反馈
            self._log_tier3_event(project_id, "parse_retry", error)
            # wrap_llm_call 失败表示调用方需要 retry，显式累加 retry_count
            if self.state_store:
                count = self.state_store.get(project_id, "tier3_retry_count") or 0
                self.state_store.set(project_id, "tier3_retry_count", count + 1)
            return response + "\n\n" + error

        return wrapped

    # =========================================================================
    # enforce_with_retry: 自动 retry
    # =========================================================================

    def enforce_with_retry(
        self,
        llm_invoke_fn: Callable,
        messages: list,
        project_id: str,
        platform: str,
        max_retries: int = None,
    ) -> Tuple[Optional[dict], str]:
        """
        自动 retry 的 enforcement。

        Args:
            llm_invoke_fn: LLM 调用函数 (messages) -> response
            messages: 初始 messages
            project_id: 项目 ID
            platform: 平台名
            max_retries: 最大重试次数（默认用平台配置）

        Returns:
            (parsed_data, last_response)
            - 成功: (dict, response)
            - 失败: (None, last_response)
        """
        config = PLATFORM_CONFIGS.get(platform, PLATFORM_CONFIGS["hermes_openclaw"])
        if max_retries is None:
            max_retries = config["max_retries"]

        current_messages = list(messages)

        for attempt in range(max_retries + 1):
            response = llm_invoke_fn(current_messages)
            data, error = self.enforce(response, platform)

            if data is not None:
                if attempt > 0:
                    self._log_tier3_event(
                        project_id, "retry_succeeded",
                        f"第 {attempt + 1} 次尝试成功"
                    )
                return data, response

            # 失败，记录并构造 retry messages
            # 每次失败都记录 "parse_retry" 事件（表示解析失败，需要 retry）
            # 但 tier3_retry_count 只在 attempt>0 时显式累加（首次失败不算 retry）
            self._log_tier3_event(project_id, "parse_retry", f"第 {attempt + 1} 次失败: {error[:200]}")
            if attempt > 0:
                # 显式累加 retry count（真正的重试次数）
                count = self.state_store.get(project_id, "tier3_retry_count") or 0 if self.state_store else 0
                if self.state_store:
                    self.state_store.set(project_id, "tier3_retry_count", count + 1)

            if attempt < max_retries:
                # 把错误反馈加入 messages 让 LLM 重写
                current_messages = current_messages + [
                    {"role": "assistant", "content": response},
                    {"role": "user", "content": error},
                ]

        # 超过 max_retries
        self._log_tier3_event(
            project_id, "max_retries_exceeded",
            f"JSON 解析失败，超过最大重试次数 {max_retries}"
        )
        return None, response

    # =========================================================================
    # 事件日志
    # =========================================================================

    def _log_tier3_event(self, project_id: str, event_type: str, detail: str):
        """记录 Tier3 事件到 StateStore（供 Coach Agent 审计）"""
        if not self.state_store:
            return

        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "layer": "tier3",
            "event_type": event_type,
            "detail": detail,
        }

        existing_log = self.state_store.get(project_id, "tier3_event_log") or []
        existing_log.append(log_entry)
        self.state_store.set(project_id, "tier3_event_log", existing_log)

        # 注意: tier3_retry_count 的累加已移到 enforce_with_retry 中显式管理
        # 这里不再自动累加，因为 wrap_llm_call 也会记录 parse_retry 事件，
        # 但它不是 retry 场景，不应该累加 retry_count

    # =========================================================================
    # 错误反馈构造
    # =========================================================================

    def _build_parse_error_feedback(
        self, parse_error: str, response: str, platform: str
    ) -> str:
        """构造解析失败的 retry 反馈"""
        # 截取 response 前 200 字符作为上下文
        preview = response[:200].replace("\n", "\\n")
        return (
            "\n[Tier3 解析失败 - 需要重试]\n"
            f"错误: {parse_error}\n"
            f"你的输出前 200 字符: {preview}...\n"
            "请重新输出合法 JSON execution_plan，要求:\n"
            "1. 首字符必须是 {\n"
            "2. 末字符必须是 }\n"
            "3. 不要输出 markdown 围栏\n"
            "4. 不要在 JSON 外输出任何说明文字\n"
            "5. 必须包含 required 字段: reframed_problem, user_constraints, plan\n"
        )

    def _build_schema_error_feedback(self, errors: list, platform: str) -> str:
        """构造 schema 验证失败的 retry 反馈"""
        errors_text = "\n".join(f"  - {e}" for e in errors[:10])  # 最多 10 条
        return (
            "\n[Tier3 Schema 验证失败 - 需要重试]\n"
            f"发现 {len(errors)} 个验证错误:\n{errors_text}\n"
            "请修正上述问题后重新输出 JSON execution_plan。\n"
            "必须包含: reframed_problem (string), user_constraints (array), plan (array)\n"
            "plan 每项必须包含: step (integer), agent, task, expected_outputs, pass_conditions\n"
        )
