"""Compile the canonical FDE roles into host-specific profile bundles."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from .registry import CompatibilityRegistry, REQUIRED_INVARIANTS


ROLE_CARDS = {
    "fde-lead": "agents/fde-lead/SKILL.md",
    "echo": "agents/echo-agent/SKILL.md",
    "delta": "agents/delta-agent/SKILL.md",
    "productize": "agents/productize-agent/SKILL.md",
    "research": "agents/research-agent/SKILL.md",
    "knowledge-curator": "agents/knowledge-curator/SKILL.md",
    "qa": "agents/qa-agent/SKILL.md",
    "legal": "agents/legal-agent/SKILL.md",
    "coach": "agents/coach-agent/SKILL.md",
}

HOST_LAYOUTS = {
    "claude_code": {"directory": ".claude/agents", "suffix": ".md", "format": "claude"},
    "gemini_cli": {"directory": ".gemini/agents", "suffix": ".md", "format": "gemini"},
    "github_copilot": {"directory": ".github/agents", "suffix": ".agent.md", "format": "copilot"},
    "opencode": {"directory": ".opencode/agents", "suffix": ".md", "format": "opencode"},
    "codex": {"directory": ".agents/skills", "suffix": "/SKILL.md", "format": "skill"},
    "hermes_agent": {"directory": ".agents/skills", "suffix": "/SKILL.md", "format": "skill"},
    "openclaw": {"directory": "skills", "suffix": "/SKILL.md", "format": "skill"},
    "workbuddy": {"directory": ".workbuddy/agents", "suffix": ".md", "format": "portable"},
    "cursor": {"directory": ".cursor/rules", "suffix": ".mdc", "format": "portable"},
}


class CompatibilityInstallError(RuntimeError):
    """Raised when compilation would overwrite user-owned host files."""


class CompatibilityCompiler:
    """Create deterministic bundles without making host capability claims."""

    def __init__(
        self,
        repository_root: str | Path,
        registry: Optional[CompatibilityRegistry] = None,
    ):
        self.repository_root = Path(repository_root).resolve()
        self.registry = registry or CompatibilityRegistry.default()

    def render(self, platform_id: str) -> dict[str, str]:
        platform = self.registry.platform(platform_id)
        layout = HOST_LAYOUTS.get(platform_id)
        files: dict[str, str] = {}
        if layout:
            for role, role_path in ROLE_CARDS.items():
                content = (self.repository_root / role_path).read_text(encoding="utf-8")
                destination = self._role_destination(layout, role)
                files[destination] = self._profile(layout["format"], role, content)

        assessment = self.registry.assess(platform_id)
        manifest = {
            "contract_version": "1.0",
            "team_id": "fde-agent-team",
            "platform_id": platform_id,
            "compatibility_mode": platform["compatibility_mode"],
            "research_confidence": platform["research_confidence"],
            "native_capabilities": assessment["native_capabilities"],
            "portable_kernel": {
                "agent_runtime": "adapters/agent_team_runtime.py",
                "coordinator": "adapters/project_coordinator.py",
                "loop": "adapters/loop_orchestrator.py",
                "policy": "config/loop-policy.json",
            },
            "required_invariants": list(REQUIRED_INVARIANTS),
            "limitations": platform.get("limitations", []),
            "roles": list(ROLE_CARDS),
        }
        files[".fde/host-manifest.json"] = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ) + "\n"
        files["FDE-TEAM.md"] = self._team_contract(platform, assessment)
        return files

    def install(
        self, platform_id: str, target: str | Path, mode: str = "fail"
    ) -> list[str]:
        if mode not in {"fail", "overwrite"}:
            raise ValueError("mode must be fail or overwrite")
        target_path = Path(target).resolve()
        files = self.render(platform_id)
        collisions = [path for path in files if (target_path / path).exists()]
        if collisions and mode == "fail":
            raise CompatibilityInstallError(
                "refusing to overwrite existing files: " + ", ".join(collisions)
            )
        for relative, content in files.items():
            destination = target_path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        return sorted(files)

    @staticmethod
    def _role_destination(layout: Mapping[str, str], role: str) -> str:
        return f"{layout['directory']}/{role}{layout['suffix']}"

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)

    def _profile(self, profile_format: str, role: str, content: str) -> str:
        body = self._strip_frontmatter(content).strip()
        description = f"FDE Agent Team 独立角色：{role}。所有产出必须返回可评分工作包。"
        if profile_format == "claude":
            header = f"---\nname: {role}\ndescription: {description}\n---"
        elif profile_format == "gemini":
            header = f"---\nname: {role}\ndescription: {description}\nkind: local\n---"
        elif profile_format == "copilot":
            header = f"---\nname: {role}\ndescription: {description}\n---"
        elif profile_format == "opencode":
            mode = "primary" if role == "fde-lead" else "subagent"
            header = f"---\ndescription: {description}\nmode: {mode}\n---"
        elif profile_format == "skill":
            header = f"---\nname: {role}\ndescription: {description}\n---"
        else:
            header = f"---\nname: {role}\ndescription: {description}\n---"
        contract = (
            "\n\n## 跨宿主执行契约\n\n"
            "你是独立 Agent 实例，不是统筹官的写作人格。只执行分配给本角色的步骤；"
            "返回产出、证据引用和约束遵循声明。统筹官评分未通过时，根据反馈在同一实例返工。"
        )
        return f"{header}\n\n{body}{contract}\n"

    @staticmethod
    def _team_contract(platform: Mapping[str, Any], assessment: Mapping[str, Any]) -> str:
        limitations = "\n".join(
            f"- {item}" for item in platform.get("limitations", [])
        ) or "- 无已知平台特有限制"
        return f"""# FDE Agent Team 宿主契约

当前宿主：`{platform['name']}`  
兼容模式：`{platform['compatibility_mode']}`  
调研置信度：`{platform['research_confidence']}`

## 不可降级的行为

1. FDE Lead 先进行苏格拉底式澄清，每轮只问一个关键问题。
2. Lead 动态拆分带依赖的步骤，并创建独立 Agent 实例执行。
3. 每步必须提交证据并经过 Lead 评分；低于阈值退回原实例。
4. 全部步骤通过后才交付用户；用户拒绝时回退根因及下游节点。
5. 所有状态、评分、返工、失效和用户反馈写入统一审计日志。
6. 宿主原生能力不能保证的部分，必须调用仓库 portable kernel，禁止用 prompt 模拟“已通过”。

## 平台限制

{limitations}

运行时入口：`adapters/agent_team_runtime.py`、`adapters/project_coordinator.py`、`adapters/loop_orchestrator.py`。
"""


__all__ = [
    "CompatibilityCompiler",
    "CompatibilityInstallError",
    "HOST_LAYOUTS",
    "ROLE_CARDS",
]
