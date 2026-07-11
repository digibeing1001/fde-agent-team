"""
FDE Agent Team - 适配器基类（v2.1 P5 实现）

定义三大平台无关接口的 Python 抽象基类：
  - FileStorage: 文件读写
  - MessageBus:  消息总线（agent 间通信）
  - StateStore:  项目状态存储

各平台适配器（feishu/langgraph/coze/dify/trae/workbuddy）继承这些基类实现具体逻辑。
C 类平台（Hermes/OpenClaw/Trae/WorkBuddy/飞书）配合 state_guard.py 使用。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class FileStorage(ABC):
    """平台无关的文件存储接口"""

    @abstractmethod
    async def read(self, path: str) -> str:
        """读取文件内容"""

    @abstractmethod
    async def write(self, path: str, content: str) -> None:
        """写入文件内容（覆盖）"""

    @abstractmethod
    async def mkdir(self, parent: str, name: str) -> str:
        """创建目录，返回目录路径/标识"""

    @abstractmethod
    async def list(self, path: str) -> list:
        """列出目录内容"""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """检查是否存在"""


class MessageBus(ABC):
    """平台无关的消息总线接口，用于 agent 间通信"""

    @abstractmethod
    async def send(self, target: str, message: str, msg_type: str = "notification") -> None:
        """发送消息到目标（agent id 或群组）"""

    @abstractmethod
    async def poll(self, filter_dict: Optional[dict] = None) -> list:
        """轮询消息（按过滤条件）"""

    @abstractmethod
    async def ack(self, message_id: str) -> None:
        """确认消息已处理"""


class StateStore(ABC):
    """平台无关的项目状态存储接口（KV 模型）"""

    @abstractmethod
    def get(self, project_id: str, key: str) -> Any:
        """获取项目级状态值"""

    @abstractmethod
    def set(self, project_id: str, key: str, value: Any) -> None:
        """设置项目级状态值"""

    @abstractmethod
    def delete(self, project_id: str, key: str) -> None:
        """删除状态"""

    @abstractmethod
    def keys(self, project_id: str) -> list:
        """列出项目的所有状态键"""


class WorkPackageConstraintsMerger:
    """
    工具函数：合并工作包约束 + LLM 在 tool_args 中传递的约束。

    设计原则（v2.1 P3 修正）:
      - 工作包中的硬约束不可被 LLM 覆盖
      - LLM 可以补充本轮新增的约束
      - 合并后由适配器代码层注入到子 agent system prompt 末尾
    """

    @staticmethod
    def merge_constraints(work_package_constraints: dict, llm_passed_constraints: dict) -> dict:
        """
        Args:
            work_package_constraints: 工作包中的硬约束（优先级高）
            llm_passed_constraints: LLM 在 call_* tool_args.user_constraints_to_pass 中传递的约束

        Returns:
            合并后的约束字典
        """
        if not work_package_constraints and not llm_passed_constraints:
            return {}
        if not work_package_constraints:
            return dict(llm_passed_constraints)
        if not llm_passed_constraints:
            return dict(work_package_constraints)

        merged = dict(work_package_constraints)  # 以工作包硬约束为基础

        # LLM 可以补充新增字段，但不能覆盖已有硬约束
        for k, v in llm_passed_constraints.items():
            if k not in merged:
                merged[k] = v
            elif k == "custom" and isinstance(merged.get(k), list) and isinstance(v, list):
                # custom 列表允许累加（去重）
                for item in v:
                    if item not in merged[k]:
                        merged[k].append(item)
            # 其他字段：工作包硬约束优先，LLM 不能覆盖

        return merged

    @staticmethod
    def format_constraints_block(constraints: dict) -> str:
        """
        把约束格式化为 system prompt 末尾的强制约束块。
        放末尾的原因：LLM 对 prompt 末尾内容记忆最强。
        """
        if not constraints:
            return ""

        lines = ["\n\n[强制用户约束 - 不可违反]"]
        if constraints.get("language"):
            lines.append(f"- 语言: {constraints['language']}")
        if constraints.get("knowledge_base"):
            lines.append(f"- 知识库: 只搜 {constraints['knowledge_base']}")
        if constraints.get("sources"):
            lines.append(f"- 限定源: {', '.join(constraints['sources'])}")
        if constraints.get("forbidden_sources"):
            lines.append(f"- 禁止源: 不要用 {', '.join(constraints['forbidden_sources'])}")
        if constraints.get("date_range"):
            lines.append(f"- 时间范围: {constraints['date_range']}")
        if constraints.get("min_sources_per_fact"):
            lines.append(f"- 最少来源: 每个事实至少 {constraints['min_sources_per_fact']} 个独立来源")
        if constraints.get("tech_stack"):
            lines.append(f"- 技术栈: {constraints['tech_stack']}")
        if constraints.get("forbidden_libs"):
            lines.append(f"- 禁止库: {', '.join(constraints['forbidden_libs'])}")
        if constraints.get("style"):
            lines.append(f"- 风格: {constraints['style']}")
        if constraints.get("audience"):
            lines.append(f"- 受众: {constraints['audience']}")
        for custom in constraints.get("custom", []):
            lines.append(f"- 自定义: {custom}")
        lines.append("违反上述任一约束 = 任务失败")
        return "\n".join(lines)


def call_worker_agent_wrapper(
    tool_name: str,
    tool_args: dict,
    work_package_constraints: dict,
    role_card_loader: Callable[[str], str],
    invoke_sub_agent_fn: Callable[[str, dict], dict],
    usage_recorder: Optional[Callable[..., Any]] = None,
):
    """
    适配器拦截 call_* 工具调用的统一入口（v2.1 P3 修正参考实现）。

    所有平台适配器必须实现等价逻辑。核心步骤：
      1. 从工作包读取完整 user_constraints（不从 LLM 输出读，避免丢失）
      2. 合并 LLM 在 tool_args.user_constraints_to_pass 中的新约束
      3. 注入到子 agent system prompt 末尾
      4. 调用子 agent
      5. 返回结果（QA Agent 在后续步骤审查约束遵循情况）

    Args:
        tool_name: 工具名（如 call_research_agent）
        tool_args: LLM 传递的工具参数
        work_package_constraints: 工作包中的硬约束
        role_card_loader: 加载子 agent role card 的函数 (agent_id -> prompt)
        invoke_sub_agent_fn: 实际调用子 agent 的函数 (prompt, args) -> result

    Returns:
        子 agent 的返回结果
    """
    # 1+2. 合并约束
    llm_passed = tool_args.get("user_constraints_to_pass", tool_args.get("user_constraints", {}))
    merged = WorkPackageConstraintsMerger.merge_constraints(work_package_constraints, llm_passed)

    # 3. 加载 role card 并注入约束块
    agent_id = _extract_agent_id_from_tool_name(tool_name)
    base_prompt = role_card_loader(agent_id)
    constraints_block = WorkPackageConstraintsMerger.format_constraints_block(merged)
    full_prompt = base_prompt + constraints_block

    # 4. 先由宿主代码记录工具调用，再调用子 agent。usage_recorder 可绑定
    # StateGuard.record_usage(project_id, **usage)，不能用 LLM 自报数字代替。
    if usage_recorder is not None:
        usage_recorder(tool_calls=1)
    result = invoke_sub_agent_fn(full_prompt, tool_args)

    # 5. 在结果中标注约束已注入（供 QA 审查）
    if isinstance(result, dict):
        result.setdefault("constraints_injected", list(merged.keys()))
    return result


def _extract_agent_id_from_tool_name(tool_name: str) -> str:
    """call_research_agent -> research"""
    if tool_name.startswith("call_") and tool_name.endswith("_agent"):
        return tool_name[5:-6] if tool_name.endswith("_agent") else tool_name[5:]
    if tool_name.startswith("call_"):
        return tool_name[5:]
    return tool_name
