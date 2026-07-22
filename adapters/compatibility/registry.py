"""Capability registry and invariant assessment for agent hosts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Optional


REQUIRED_INVARIANTS = (
    "independent_agents",
    "isolated_context",
    "dynamic_delegation",
    "persistent_state",
    "deterministic_step_gates",
    "human_feedback_loop",
    "audit_trace",
    "tool_policy",
)

PORTABLE_KERNEL_CAPABILITIES = {
    "independent_agents",
    "persistent_state",
    "deterministic_step_gates",
    "human_feedback_loop",
    "audit_trace",
}


class CompatibilityRegistry:
    """Load researched host facts and calculate effective compatibility."""

    def __init__(self, data: Mapping[str, Any]):
        self.data = copy.deepcopy(dict(data))
        self._platforms = {
            item["id"]: item for item in self.data.get("platforms", [])
        }
        self._validate()

    @classmethod
    def from_file(cls, path: str | Path) -> "CompatibilityRegistry":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def default(cls) -> "CompatibilityRegistry":
        path = Path(__file__).resolve().parents[2] / "config" / "host-capabilities.json"
        return cls.from_file(path)

    def platform(self, platform_id: str) -> dict[str, Any]:
        try:
            return copy.deepcopy(self._platforms[platform_id])
        except KeyError as exc:
            raise KeyError(f"unknown agent host: {platform_id}") from exc

    def list_platforms(self, priority: Optional[str] = None) -> list[dict[str, Any]]:
        items = list(self._platforms.values())
        if priority is not None:
            items = [item for item in items if item["priority"] == priority]
        return copy.deepcopy(items)

    def assess(self, platform_id: str) -> dict[str, Any]:
        platform = self.platform(platform_id)
        native = set(platform.get("native_capabilities", []))
        effective = native | PORTABLE_KERNEL_CAPABILITIES
        if platform.get("execution_bridge"):
            effective.add("dynamic_delegation")
            effective.add("isolated_context")
        if platform.get("tool_policy_bridge"):
            effective.add("tool_policy")
        missing = sorted(set(REQUIRED_INVARIANTS) - effective)
        return {
            "platform_id": platform_id,
            "name": platform["name"],
            "priority": platform["priority"],
            "compatibility_mode": platform["compatibility_mode"],
            "research_confidence": platform["research_confidence"],
            "native_capabilities": sorted(native),
            "portable_kernel_capabilities": sorted(PORTABLE_KERNEL_CAPABILITIES),
            "effective_capabilities": sorted(effective),
            "missing_invariants": missing,
            "contract_compatible": not missing,
            "native_parity": set(REQUIRED_INVARIANTS).issubset(native),
            "limitations": copy.deepcopy(platform.get("limitations", [])),
        }

    def matrix(self) -> list[dict[str, Any]]:
        return [self.assess(item["id"]) for item in self.list_platforms()]

    def _validate(self) -> None:
        if self.data.get("schema_version") != "1.0":
            raise ValueError("unsupported host capability schema")
        if not self._platforms:
            raise ValueError("platform registry cannot be empty")
        if len(self._platforms) != len(self.data["platforms"]):
            raise ValueError("platform ids must be unique")
        for platform in self._platforms.values():
            for field in (
                "name",
                "category",
                "priority",
                "compatibility_mode",
                "research_confidence",
                "execution_bridge",
                "tool_policy_bridge",
                "sources",
            ):
                if field not in platform:
                    raise ValueError(f"{platform['id']} missing {field}")
            if not platform["sources"]:
                raise ValueError(f"{platform['id']} must have research sources")


__all__ = [
    "CompatibilityRegistry",
    "PORTABLE_KERNEL_CAPABILITIES",
    "REQUIRED_INVARIANTS",
]
