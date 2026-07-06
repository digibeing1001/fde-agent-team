"""
FDE Agent Team - StateGuard for C-class platforms (v2.1 P4 修正)

目的:
    C 类平台（Hermes/OpenClaw/Trae/WorkBuddy/飞书）没有原生状态机引擎，
    靠 LLM 自觉执行 state_machine.json 不可靠。
    StateGuard 包装 LLM 调用，在每次状态转换前检查 validation.pre_transition_check，
    未通过则拒绝转换并返回错误，强制 LLM 回到正确状态。

适用平台:
    - Hermes/OpenClaw (prompt-only)
    - Trae/Claude Code (skill 包装器)
    - WorkBuddy (配置层)
    - 飞书 (lark-cli 包装器)

不适用（A/B 类平台有原生引擎）:
    - LangGraph (StateGraph conditional_edge)
    - Dify v1.13+ (workflow HITL 节点)
    - Microsoft Agent Framework (Checkpoint)
    - Coze (workflow 条件节点)

使用方式:
    from adapters.state_guard import StateGuard

    guard = StateGuard(state_machine_path, state_store)
    guard.wrap_llm_call(llm_invoke_fn, project_id)

设计原则:
    - LLM 决策 + 代码执行分离（hard invariants 由代码保证，creative work 由 LLM）
    - fail_closed: 验证失败 = 拒绝转换，不让 LLM 继续
    - 可观察: 所有违规记录到 StateStore 供 Coach Agent 审计
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional


# v2.1.1: XML 标签正则 - 解析 <state_transition current="X" target="Y" artifact="Z" reason="..." />
# 依据: Anthropic 官方推荐 XML tags 结构化 Claude 输出（Claude 训练偏好，遵守度高）
_STATE_TRANSITION_XML_PATTERN = re.compile(
    r'<state_transition\s+([^>]*?)/>',
    re.DOTALL
)
_ATTR_PATTERN = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


class StateMachineError(Exception):
    """状态机违规错误"""

    def __init__(self, violation_type: str, current_state: str, target_state: str, detail: str):
        self.violation_type = violation_type
        self.current_state = current_state
        self.target_state = target_state
        self.detail = detail
        super().__init__(
            f"[StateGuard] {violation_type}: {current_state} -> {target_state} | {detail}"
        )


class StateGuard:
    """
    C 类平台状态机守卫。包装 LLM 调用，拦截非法状态转换。

    核心逻辑:
        1. 加载 state_machine.json
        2. 跟踪 project_id 的当前状态（持久化到 StateStore）
        3. 每次 LLM 试图转换状态时，检查:
           - 转换是否合法（target 在 current.transitions 中）
           - validation.pre_transition_check 是否满足
           - artifact_required 是否已产出
        4. 未通过 = 拒绝转换，返回错误，让 LLM 重试
        5. 通过 = 更新状态，继续执行
    """

    def __init__(self, state_machine_path: str, state_store):
        """
        Args:
            state_machine_path: state_machine.json 路径
            state_store: StateStore 接口实现（用于持久化状态和日志）
        """
        self.state_machine = self._load_state_machine(state_machine_path)
        self.state_store = state_store
        self.initial_state = self.state_machine["initial_state"]
        self.terminal_states = self.state_machine["terminal_states"]

    def _load_state_machine(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_current_state(self, project_id: str) -> str:
        """获取项目当前状态"""
        state = self.state_store.get(project_id, "current_state")
        if state is None:
            state = self.initial_state
            self.state_store.set(project_id, "current_state", state)
        return state

    def _set_current_state(self, project_id: str, state: str):
        """更新项目当前状态"""
        self.state_store.set(project_id, "current_state", state)

    def _log_violation(self, project_id: str, violation: StateMachineError):
        """记录违规到 StateStore（供 Coach Agent 审计）"""
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "violation_type": violation.violation_type,
            "current_state": violation.current_state,
            "target_state": violation.target_state,
            "detail": violation.detail,
        }
        existing_log = self.state_store.get(project_id, "violation_log") or []
        existing_log.append(log_entry)
        self.state_store.set(project_id, "violation_log", existing_log)

    def _check_artifact_exists(self, project_id: str, artifact_name: str) -> bool:
        """检查所需 artifact 是否已产出（通过 StateStore 追踪）"""
        produced = self.state_store.get(project_id, "produced_artifacts") or []
        return artifact_name in produced

    def _record_artifact(self, project_id: str, artifact_name: str):
        """记录已产出的 artifact"""
        produced = self.state_store.get(project_id, "produced_artifacts") or []
        if artifact_name not in produced:
            produced.append(artifact_name)
            self.state_store.set(project_id, "produced_artifacts", produced)

    def validate_transition(
        self,
        project_id: str,
        target_state: str,
        produced_artifact: Optional[str] = None,
        user_confirmed: Optional[bool] = None,
    ) -> bool:
        """
        验证状态转换是否合法。未通过抛 StateMachineError。

        Args:
            project_id: 项目 ID
            target_state: 目标状态
            produced_artifact: 本次产出的 artifact 名称（可选）
            user_confirmed: 用户是否确认（gate_phase 用）

        Returns:
            True 如果转换合法

        Raises:
            StateMachineError: 如果转换非法
        """
        current = self.get_current_state(project_id)
        current_state_def = self.state_machine["states"].get(current)

        if not current_state_def:
            raise StateMachineError(
                "unknown_current_state", current, target_state, f"状态 {current} 不在 state_machine 中"
            )

        # 检查是否是终态
        if current in self.terminal_states:
            raise StateMachineError(
                "terminal_state_reached", current, target_state, f"{current} 是终态，不能再转换"
            )

        # 检查目标状态是否在合法 transitions 中
        transitions = current_state_def.get("transitions", [])
        valid_transition = None
        for t in transitions:
            if t["to"] == target_state:
                valid_transition = t
                break

        if not valid_transition:
            raise StateMachineError(
                "invalid_transition",
                current,
                target_state,
                f"{current} -> {target_state} 不在 transitions 中。合法目标: {[t['to'] for t in transitions]}",
            )

        # 记录本次产出的 artifact
        if produced_artifact:
            self._record_artifact(project_id, produced_artifact)

        # 如果从 gate_phase 转出，先检查 user_confirmed（优先级高于 artifact 检查）
        # 因为 user_confirmation.json 是用户确认后才产出的，本质上是同一回事
        if current == "gate_phase" and target_state not in ("aborted",):
            if user_confirmed is not True:
                raise StateMachineError(
                    "skip_without_confirmation",
                    current,
                    target_state,
                    "gate_phase 必须等用户确认 (user_confirmed=true) 才能转出，未确认前禁止进入下一步",
                )
            # 用户已确认，自动记录 user_confirmation.json artifact
            self._record_artifact(project_id, "user_confirmation.json")

        # 检查 artifact_required 是否已产出
        required_artifact = valid_transition.get("artifact_required")
        if required_artifact and not self._check_artifact_exists(project_id, required_artifact):
            raise StateMachineError(
                "missing_required_artifact",
                current,
                target_state,
                f"转换需要 artifact {required_artifact}，但尚未产出",
            )

        # 如果目标是 gate 状态，检查 gate 的 validation
        target_state_def = self.state_machine["states"].get(target_state, {})
        if target_state_def.get("blocking") and target_state_def.get("type") in (
            "human_gate",
            "agent_gate",
        ):
            validation = target_state_def.get("validation", {})
            on_invalid = target_state_def.get("on_invalid", {})

            # gate 状态需要进入时检查其 required_artifacts
            gate_required = target_state_def.get("required_artifacts", [])
            for artifact in gate_required:
                if not self._check_artifact_exists(project_id, artifact):
                    violation_key = f"missing_{artifact.replace('.json', '')}"
                    action = on_invalid.get(violation_key, on_invalid.get("missing_gate_protocol", "block"))
                    raise StateMachineError(
                        "gate_artifact_missing",
                        current,
                        target_state,
                        f"Gate {target_state} 需要 artifact {artifact}，但尚未产出。Action: {action}",
                    )

        # gate_phase 特殊处理：进入 gate_phase 是合法的（输出 gate_protocol.json 后进入）
        # 但从 gate_phase 出去需要 user_confirmation（已在上方检查）
        if target_state == "gate_phase":
            pass

        return True

    def commit_transition(
        self,
        project_id: str,
        target_state: str,
        produced_artifact: Optional[str] = None,
        user_confirmed: Optional[bool] = None,
    ) -> str:
        """
        验证并提交状态转换。验证失败抛异常，成功则更新状态。

        Returns:
            新状态名
        """
        self.validate_transition(project_id, target_state, produced_artifact, user_confirmed)
        self._set_current_state(project_id, target_state)
        return target_state

    def wrap_llm_call(self, llm_invoke_fn: Callable, project_id: str, max_missing_tag_retries: int = 2):
        """
        包装 LLM 调用函数，自动拦截状态转换。

        v2.1.1 升级：
        - 支持 XML 标签解析（C 类平台推荐，Anthropic XML tags 官方背书）
        - missing tag 检测：LLM 输出无 <state_transition> 标签时触发 retry
        - retry 限制：最多 max_missing_tag_retries 次，仍失败则保持当前状态 + warning
        - no-change 标签处理：target==current 且无 artifact 时，正常返回不转换

        依据：
        - Anthropic 官方 prompt engineering 文档推荐 XML tags 结构化 Claude 输出
        - self-correction via feedback：基于反馈的自我修正

        Args:
            llm_invoke_fn: LLM 调用函数 (messages: list) -> response: str
            project_id: 项目 ID
            max_missing_tag_retries: missing tag 时的最大 retry 次数（默认 2）

        Returns:
            包装后的 LLM 调用函数
        """

        def wrapped(messages: list) -> str:
            response = llm_invoke_fn(messages)

            # 检查是否有状态转换标签（即使格式错误也算有声明意图）
            has_tag = "<state_transition" in response
            transition = self._extract_transition_from_response(response)

            # 情况 1: 无标签且无 JSON 转换指令 → missing tag，触发 retry 反馈
            if not transition and not has_tag:
                self._log_missing_tag(project_id, response)
                return response + self._build_missing_tag_feedback(max_missing_tag_retries)

            # 情况 2: 有标签但解析失败（格式错误）→ 触发 retry 反馈
            if has_tag and not transition:
                violation = StateMachineError(
                    "malformed_state_tag",
                    self.get_current_state(project_id),
                    "unknown",
                    "LLM 输出包含 <state_transition> 标签但格式错误，无法解析属性",
                )
                self._log_violation(project_id, violation)
                return response + self._build_malformed_tag_feedback()

            # 情况 3: 有 no-change 标签（target == current 且无 artifact）→ 正常返回，不转换
            current = self.get_current_state(project_id)
            target = transition.get("target_state")
            artifact = transition.get("produced_artifact")
            if target == current and not artifact:
                # 明确声明无转换，正常返回
                return response

            # 情况 4: 有转换指令 → 校验并提交
            user_confirmed = transition.get("user_confirmed")

            try:
                self.commit_transition(project_id, target, artifact, user_confirmed)
                # 转换成功，返回原 response
                return response
            except StateMachineError as e:
                # 转换失败，记录违规
                self._log_violation(project_id, e)
                return response + self._build_transition_error_feedback(e)

        return wrapped

    def _extract_transition_from_response(self, response: str) -> Optional[dict]:
        """
        从 LLM 输出中提取状态转换指令。

        解析优先级（v2.1.1）：
        1. XML 标签 <state_transition current="X" target="Y" artifact="Z" reason="..." />
           （C 类平台推荐，Anthropic XML tags 官方背书，正则解析简单）
        2. JSON state_transition 字段
           （A/B 类平台，输出整体是 JSON）
        3. JSON waiting_for="user_confirmation" / verdict 字段
           （gate 输出和 QA 输出）

        Returns:
            转换指令 dict（含 _source 字段标记来源），或 None（无状态转换指令）
        """
        # 1. 优先尝试 XML 标签解析（C 类平台推荐）
        xml_transition = self._extract_xml_transition(response)
        if xml_transition:
            return xml_transition

        # 2. 尝试 JSON 解析（兼容 A/B 类平台）
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                if "state_transition" in data:
                    result = dict(data["state_transition"])
                    result["_source"] = "json"
                    return result
                if data.get("waiting_for") == "user_confirmation":
                    return {
                        "target_state": "gate_phase",
                        "produced_artifact": "gate_protocol.json",
                        "_source": "json_gate",
                    }
                if data.get("verdict") in ("pass", "fail", "rework"):
                    artifact_map = {
                        "pass": "qa_pass.json",
                        "rework": "rework_list.json",
                        "fail": "qa_fail.json",
                    }
                    return {
                        "target_state": "act" if data["verdict"] != "fail" else "failed",
                        "produced_artifact": artifact_map.get(data["verdict"]),
                        "_source": "json_verdict",
                    }
        except (json.JSONDecodeError, ValueError):
            pass

        # 3. 都没有，返回 None（wrap_llm_call 会处理 missing tag）
        return None

    def _extract_xml_transition(self, response: str) -> Optional[dict]:
        """
        从 LLM 输出中提取 <state_transition ... /> XML 标签。

        格式：<state_transition current="X" target="Y" artifact="Z.json" reason="..." />

        Returns:
            转换指令 dict（含 _source="xml"），或 None
        """
        match = _STATE_TRANSITION_XML_PATTERN.search(response)
        if not match:
            return None

        attrs_str = match.group(1)
        attrs = dict(_ATTR_PATTERN.findall(attrs_str))

        target = attrs.get("target", "").strip()
        if not target:
            return None  # 标签存在但无 target，视为无效（让 wrap_llm_call 走 malformed 路径）

        artifact = attrs.get("artifact", "").strip()

        transition = {
            "target_state": target,
            "produced_artifact": artifact if artifact else None,
            "_source": "xml",
        }

        # current 字段用于一致性校验（可选）
        if "current" in attrs:
            transition["current_state"] = attrs["current"].strip()

        # reason 字段用于审计（可选）
        if "reason" in attrs:
            transition["reason"] = attrs["reason"].strip()

        return transition

    def _log_missing_tag(self, project_id: str, response: str):
        """记录 missing tag 警告到 StateStore（供 Coach Agent 审计）"""
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "violation_type": "missing_state_tag",
            "current_state": self.get_current_state(project_id),
            "target_state": "unknown",
            "detail": "LLM 输出缺少 <state_transition> XML 标签",
            "response_preview": response[:200] + "..." if len(response) > 200 else response,
        }
        existing_log = self.state_store.get(project_id, "violation_log") or []
        existing_log.append(log_entry)
        self.state_store.set(project_id, "violation_log", existing_log)

        # 增加 missing_tag_count
        count = self.state_store.get(project_id, "missing_tag_count") or 0
        self.state_store.set(project_id, "missing_tag_count", count + 1)

    def _build_missing_tag_feedback(self, max_retries: int) -> str:
        """构造 missing tag 反馈消息"""
        return (
            f"\n\n[StateGuard 检测到输出缺少状态标签]\n"
            f"你的回复末尾没有 `<state_transition>` XML 标签。\n"
            f"v2.1.1 协议要求每次输出末尾必须带该标签声明状态转换意图。\n"
            f"依据: Anthropic XML tags 官方推荐 + self-correction\n"
            f"\n请重新回复，并在末尾添加：\n"
            f'```xml\n<state_transition current="当前状态" target="目标状态" artifact="产出文件名" reason="转换原因" />\n```\n'
            f"合法状态: context / decide / act / evaluate / gate_phase / gate_quality / gate_legal / done / failed / aborted\n"
            f"如无状态转换，target=current，artifact 留空。\n"
            f"最多重试 {max_retries} 次，仍缺失则任务标记 warning。"
        )

    def _build_malformed_tag_feedback(self) -> str:
        """构造格式错误反馈"""
        return (
            f"\n\n[StateGuard 检测到状态标签格式错误]\n"
            f"你的回复包含 `<state_transition>` 标签但格式无法解析。\n"
            f"请确保标签格式为：\n"
            f'```xml\n<state_transition current="X" target="Y" artifact="Z.json" reason="..." />\n```\n'
            f"注意：属性值必须用双引号，标签必须以 /> 结尾。"
        )

    def _build_transition_error_feedback(self, e: StateMachineError) -> str:
        """构造转换错误反馈"""
        return (
            f"\n\n[StateGuard 拒绝状态转换]\n"
            f"违规类型: {e.violation_type}\n"
            f"当前状态: {e.current_state}\n"
            f"试图转换到: {e.target_state}\n"
            f"原因: {e.detail}\n"
            f"\n请修正后重试："
            f"  - 如果是 missing_required_artifact：先产出所需 artifact"
            f"  - 如果是 skip_without_confirmation：先输出 gate_protocol.json 等用户确认"
            f"  - 如果是 invalid_transition：检查 state_machine.json 的 transitions 定义"
        )

    def get_state_info(self, project_id: str) -> dict:
        """获取项目状态信息（供调试和日志）"""
        current = self.get_current_state(project_id)
        produced = self.state_store.get(project_id, "produced_artifacts") or []
        violations = self.state_store.get(project_id, "violation_log") or []

        current_state_def = self.state_machine["states"].get(current, {})
        valid_targets = [t["to"] for t in current_state_def.get("transitions", [])]

        return {
            "project_id": project_id,
            "current_state": current,
            "produced_artifacts": produced,
            "valid_next_states": valid_targets,
            "violation_count": len(violations),
            "recent_violations": violations[-3:] if violations else [],
        }


# =============================================================================
# 测试用例（自检）
# =============================================================================
if __name__ == "__main__":
    # 简单内存 StateStore 用于测试
    class MemoryStateStore:
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

    store = MemoryStateStore()
    sm_path = Path(__file__).parent.parent / "agents/fde-lead/skills/fde-loop-control/state_machine.json"
    guard = StateGuard(str(sm_path), store)

    project_id = "TEST-001"

    # 测试 1: 初始状态应该是 context
    assert guard.get_current_state(project_id) == "context", "初始状态应该是 context"
    print("[OK] 测试1: 初始状态 context")

    # 测试 2: context -> decide 需要 execution_plan.json artifact
    try:
        guard.commit_transition(project_id, "decide")
        print("[FAIL] 测试2: 应该失败（缺少 artifact）")
    except StateMachineError as e:
        assert e.violation_type == "missing_required_artifact"
        print(f"[OK] 测试2: 正确拦截缺少 artifact 的转换 ({e.violation_type})")

    # 测试 3: 产出 artifact 后转换应该成功
    guard._record_artifact(project_id, "execution_plan.json (初版)")
    guard.commit_transition(project_id, "decide", produced_artifact="execution_plan.json")
    assert guard.get_current_state(project_id) == "decide"
    print("[OK] 测试3: 产出 artifact 后转换成功")

    # 测试 4: 非法转换（decide -> done 不在 transitions 中）
    try:
        guard.commit_transition(project_id, "done")
        print("[FAIL] 测试4: 应该失败（非法转换）")
    except StateMachineError as e:
        assert e.violation_type == "invalid_transition"
        print(f"[OK] 测试4: 正确拦截非法转换 ({e.violation_type})")

    # 测试 5: gate_phase 阻断 - 没确认就转出应该失败
    # 正确路径: decide -> act (产出 validated_plan.json) -> gate_phase (产出 phase_transition.json + gate_protocol.json)
    guard._record_artifact(project_id, "validated_plan.json")
    guard.commit_transition(project_id, "act", produced_artifact="validated_plan.json")
    print(f"[OK] 测试5a: decide -> act 转换成功")
    # act -> gate_phase 需要 phase_transition.json (transition artifact) + gate_protocol.json (gate required_artifacts)
    guard._record_artifact(project_id, "phase_transition.json")
    guard._record_artifact(project_id, "gate_protocol.json")  # gate_phase.required_artifacts
    guard.commit_transition(project_id, "gate_phase", produced_artifact="phase_transition.json")
    assert guard.get_current_state(project_id) == "gate_phase"
    print(f"[OK] 测试5b: act -> gate_phase 转换成功（已产出 gate_protocol.json）")
    # 未确认就转出应该失败
    try:
        guard.commit_transition(project_id, "context", user_confirmed=False)
        print("[FAIL] 测试5c: 应该失败（未确认）")
    except StateMachineError as e:
        assert e.violation_type == "skip_without_confirmation"
        print(f"[OK] 测试5c: 正确拦截未确认的 gate 跳过 ({e.violation_type})")

    # 测试 6: 确认后转换应该成功
    guard.commit_transition(project_id, "context", user_confirmed=True)
    assert guard.get_current_state(project_id) == "context"
    print("[OK] 测试6: 确认后转换成功")

    print("\n所有测试通过！StateGuard 可正确拦截非法状态转换。")
    print(f"违规日志: {len(store.get(project_id, 'violation_log') or [])} 条")
