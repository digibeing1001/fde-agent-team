"""Independent agent instances managed by the FDE team secretary.

Feishu exposes one application bot (the secretary), while every worker remains
an independent runtime actor with its own instance id, inbox, state and output
stream.  The shared bot is a transport adapter, not a replacement for workers.
"""

from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Callable, Iterable, Mapping, Optional


DEFAULT_ROLES = (
    "echo",
    "delta",
    "productize",
    "research",
    "knowledge-curator",
    "qa",
    "legal",
    "coach",
)


class AgentTeamError(ValueError):
    """Raised when an independent team cannot be provisioned safely."""


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AgentTeamRuntime:
    """Provision and track one isolated agent instance per project role."""

    storage_key = "agent_team_runtime_v1"

    def __init__(
        self,
        state_store: Any,
        roles: Iterable[str] = DEFAULT_ROLES,
        clock: Optional[Callable[[], str]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ):
        self.state_store = state_store
        self.roles = tuple(dict.fromkeys(str(role) for role in roles))
        if not self.roles or any(not role for role in self.roles):
            raise AgentTeamError("roles must contain non-empty unique role names")
        self.clock = clock or _utc_timestamp
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def provision(
        self,
        project_id: str,
        group_chat_id: str,
        requested_roles: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        """Create independent worker instances and bind them to one project group."""
        if not project_id or not group_chat_id:
            raise AgentTeamError("project_id and group_chat_id are required")
        if self.state_store.get(project_id, self.storage_key) is not None:
            raise AgentTeamError(f"team already exists for project {project_id}")
        selected = tuple(dict.fromkeys(requested_roles or self.roles))
        unknown = sorted(set(selected) - set(self.roles))
        if unknown:
            raise AgentTeamError(f"unknown roles: {unknown}")

        agents = []
        for role in selected:
            agents.append(
                {
                    "instance_id": f"{role}-{self.id_factory()}",
                    "role": role,
                    "project_id": project_id,
                    "group_chat_id": group_chat_id,
                    "lifecycle": "created",
                    "inbox": [],
                    "outbox": [],
                    "working_state": {},
                    "created_at": self.clock(),
                    "activated_at": None,
                }
            )
        team = {
            "schema_version": "1.0",
            "project_id": project_id,
            "group_chat_id": group_chat_id,
            "secretary": {
                "role": "fde-lead",
                "is_feishu_bot": True,
                "responsibility": "orchestration_and_shared_transport",
            },
            "agents": agents,
            "transport": {
                "visible_feishu_bots": 1,
                "shared_bot_role": "fde-lead",
                "identity_mode": "role_signed_message",
            },
            "status": "provisioned",
            "created_at": self.clock(),
            "updated_at": self.clock(),
        }
        self._persist(team)
        return copy.deepcopy(team)

    def activate(self, project_id: str) -> dict[str, Any]:
        """Activate all provisioned agents after the intake gate is complete."""
        team = self.get(project_id)
        for agent in team["agents"]:
            agent["lifecycle"] = "active"
            agent["activated_at"] = self.clock()
        team["status"] = "active"
        self._persist(team)
        return copy.deepcopy(team)

    def dispatch(
        self, project_id: str, role: str, task: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Put a task into exactly one independent agent inbox."""
        team = self.get(project_id)
        if team["status"] != "active":
            raise AgentTeamError("team must be active before dispatch")
        agent = self._agent(team, role)
        envelope = {
            "message_id": self.id_factory(),
            "task": copy.deepcopy(dict(task)),
            "created_at": self.clock(),
        }
        agent["inbox"].append(envelope)
        self._persist(team)
        return copy.deepcopy(envelope)

    def publish(
        self, project_id: str, role: str, content: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Record a worker result with an immutable role/instance signature."""
        team = self.get(project_id)
        agent = self._agent(team, role)
        envelope = {
            "message_id": self.id_factory(),
            "agent_instance_id": agent["instance_id"],
            "role": role,
            "content": copy.deepcopy(dict(content)),
            "created_at": self.clock(),
        }
        agent["outbox"].append(envelope)
        self._persist(team)
        return copy.deepcopy(envelope)

    def get(self, project_id: str) -> dict[str, Any]:
        team = self.state_store.get(project_id, self.storage_key)
        if team is None:
            raise AgentTeamError(f"team not found for project {project_id}")
        return copy.deepcopy(team)

    def roster_markdown(self, project_id: str) -> str:
        team = self.get(project_id)
        lines = [
            "## FDE 项目团队已组建",
            "",
            "飞书入口：团队统筹官（秘书）",
            "",
            "独立 Agent 实例：",
        ]
        lines.extend(
            f"- `{agent['role']}` · `{agent['instance_id']}` · {agent['lifecycle']}"
            for agent in team["agents"]
        )
        lines.append("")
        lines.append("所有 Agent 独立执行；消息由统筹官统一转发并保留角色签名。")
        return "\n".join(lines)

    def _agent(self, team: Mapping[str, Any], role: str) -> dict[str, Any]:
        for agent in team["agents"]:
            if agent["role"] == role:
                return agent
        raise AgentTeamError(f"role {role} is not part of this project")

    def _persist(self, team: dict[str, Any]) -> None:
        team["updated_at"] = self.clock()
        self.state_store.set(
            team["project_id"], self.storage_key, copy.deepcopy(team)
        )


__all__ = ["AgentTeamError", "AgentTeamRuntime", "DEFAULT_ROLES"]
