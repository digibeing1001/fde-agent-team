"""Controlled Feishu group gateway for one project-scoped FDE agent team."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


HANDOFF_PREFIX = "[FDE_HANDOFF_V1]"
MAX_BOTS_PER_CHAT = 15
MAX_BOTS_PER_INVITE = 5


class GatewayError(ValueError):
    """Manifest or handoff violates the gateway contract."""


def invite_batch_sizes(bot_count: int) -> list[int]:
    """Split the actual confirmed roster by Feishu's per-request ceiling."""
    if bot_count < 1 or bot_count > MAX_BOTS_PER_CHAT:
        raise GatewayError("confirmed bot count must be between 1 and 15")
    return [
        min(MAX_BOTS_PER_INVITE, bot_count - start)
        for start in range(0, bot_count, MAX_BOTS_PER_INVITE)
    ]


@dataclass(frozen=True)
class RouteDecision:
    accepted: bool
    reason: str
    envelope: dict[str, Any] | None = None


def _agents(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in manifest.get("agents", [])]


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    required = ("team_id", "project_id", "chat_id_env", "secretary_agent_id", "agents")
    missing = [key for key in required if not manifest.get(key)]
    if missing:
        raise GatewayError(f"manifest missing: {', '.join(missing)}")
    agents = _agents(manifest)
    if not 1 <= len(agents) <= MAX_BOTS_PER_CHAT:
        raise GatewayError("a Feishu chat must contain between 1 and 15 bots")
    ids = [str(item.get("agent_id", "")) for item in agents]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise GatewayError("agent_id values must be present and unique")
    if manifest["secretary_agent_id"] not in ids:
        raise GatewayError("secretary_agent_id must identify an agent in this team")
    for agent in agents:
        for key in ("profile", "app_id_env", "open_id_env"):
            if not agent.get(key):
                raise GatewayError(f"agent {agent.get('agent_id')} missing {key}")


def inventory_environment(
    manifest: Mapping[str, Any], inventory: Mapping[str, Any] | None,
    base: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """Merge reusable non-secret Bot IDs using the stable team_id/agent_id key."""
    result = dict(base)
    if not inventory:
        return result
    if inventory.get("version") == "2.0.0":
        records = inventory.get("bots", {})
        if not isinstance(records, Mapping):
            raise GatewayError("bot inventory bots must be an object")
        key_for = lambda agent_id: f"{manifest['team_id']}/{agent_id}"
    else:
        if inventory.get("team_id") and inventory.get("team_id") != manifest["team_id"]:
            return result
        records = inventory.get("agents", {})
        if not isinstance(records, Mapping):
            raise GatewayError("bot inventory agents must be an object")
        key_for = lambda agent_id: agent_id
    for agent in _agents(manifest):
        record = records.get(key_for(agent["agent_id"]), {})
        if not isinstance(record, Mapping):
            continue
        if record.get("app_id"):
            result.setdefault(agent["app_id_env"], str(record["app_id"]))
        if record.get("open_id"):
            result.setdefault(agent["open_id_env"], str(record["open_id"]))
    return result


def staffing_proposal(
    manifest: Mapping[str, Any], *, objective: str, specialists: list[str],
) -> dict[str, Any]:
    """Create the fde-lead secretary/dispatcher proposal for human confirmation."""
    validate_manifest(manifest)
    if not objective.strip():
        raise GatewayError("staffing objective is required")
    known = {item["agent_id"] for item in _agents(manifest)}
    unknown = sorted(set(specialists) - known)
    if unknown:
        raise GatewayError(f"unknown specialists: {', '.join(unknown)}")
    core = [manifest["secretary_agent_id"]]
    selected_set = set(core + specialists)
    selected = [item["agent_id"] for item in _agents(manifest) if item["agent_id"] in selected_set]
    proposal = {
        "schema_version": "1.0", "kind": "feishu-team-staffing-proposal",
        "team_id": manifest["team_id"], "project_id": manifest["project_id"],
        "objective": objective.strip(), "core_agents": core,
        "selected_agents": selected, "requires_human_confirmation": True,
    }
    canonical = json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    proposal["confirmation_token"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]
    return proposal


def provision_plan(
    manifest: Mapping[str, Any], env: Mapping[str, str] = os.environ, *,
    proposal: Mapping[str, Any], confirmation_token: str,
) -> list[list[str]]:
    """Return argv-only commands after confirmation; never changes Feishu state."""
    validate_manifest(manifest)
    expected = staffing_proposal(
        manifest, objective=str(proposal.get("objective", "")),
        specialists=[
            str(agent_id) for agent_id in proposal.get("selected_agents", [])
            if agent_id != manifest["secretary_agent_id"]
        ],
    )
    if proposal.get("confirmation_token") != expected["confirmation_token"]:
        raise GatewayError("staffing proposal was modified after it was issued")
    if not confirmation_token or confirmation_token != expected["confirmation_token"]:
        raise GatewayError("the exact staffing proposal has not been confirmed")
    selected = set(expected["selected_agents"])
    agents = [item for item in _agents(manifest) if item["agent_id"] in selected]
    app_ids = []
    for agent in agents:
        app_id = env.get(agent["app_id_env"], "")
        if not app_id:
            raise GatewayError(f"environment variable {agent['app_id_env']} is required")
        app_ids.append(app_id)
    owner = next(item for item in agents if item["agent_id"] == manifest["secretary_agent_id"])
    first, rest = app_ids[:MAX_BOTS_PER_INVITE], app_ids[MAX_BOTS_PER_INVITE:]
    commands = [[
        "lark-cli", "--profile", owner["profile"], "im", "+chat-create",
        "--name", manifest.get("chat_name", manifest["team_id"]),
        "--description", manifest.get("description", f"Project {manifest['project_id']} agent team"),
        "--bots", ",".join(first), "--type", "private", "--set-bot-manager", "--as", "bot",
    ]]
    chat_ref = f"${{{manifest['chat_id_env']}}}"
    for offset in range(0, len(rest), MAX_BOTS_PER_INVITE):
        batch = rest[offset:offset + MAX_BOTS_PER_INVITE]
        commands.append([
            "lark-cli", "--profile", owner["profile"], "im", "chat.members", "create",
            "--chat-id", chat_ref, "--member-id-type", "app_id",
            "--data", json.dumps({"id_list": batch}, separators=(",", ":")), "--as", "bot",
        ])
    return commands


def build_handoff(
    manifest: Mapping[str, Any], *, sender: str, target: str, task: str,
    correlation_id: str, hop: int = 1, visited_edges: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
) -> tuple[str, str]:
    validate_manifest(manifest)
    by_id = {item["agent_id"]: item for item in _agents(manifest)}
    if sender == target or sender not in by_id or target not in by_id:
        raise GatewayError("sender and target must be different agents in this team")
    edge = f"{sender}->{target}"
    visited = list(visited_edges or [])
    if edge in visited:
        raise GatewayError("repeated routing edge would create a loop")
    open_id = env.get(by_id[target]["open_id_env"], "")
    if not open_id:
        raise GatewayError(f"environment variable {by_id[target]['open_id_env']} is required")
    envelope = {
        "schema_version": "1.0", "team_id": manifest["team_id"],
        "project_id": manifest["project_id"], "from": sender, "to": target,
        "task": task, "correlation_id": correlation_id, "hop": hop,
        "visited_edges": visited + [edge],
    }
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    text = f'<at user_id="{open_id}">{target}</at> {HANDOFF_PREFIX}\n{payload}'
    idempotency_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]
    return text, idempotency_key


def _mention_ids(event: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for mention in event.get("mentions", []) or []:
        value = mention.get("id", "") if isinstance(mention, Mapping) else ""
        if isinstance(value, Mapping):
            value = value.get("open_id", "")
        if value:
            result.add(str(value))
    return result


def _parse_envelope(content: str) -> dict[str, Any]:
    marker = content.find(HANDOFF_PREFIX)
    if marker < 0:
        raise GatewayError("bot message is missing the typed handoff envelope")
    raw = content[marker + len(HANDOFF_PREFIX):].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GatewayError("handoff envelope is not valid JSON") from exc
    if not isinstance(value, dict):
        raise GatewayError("handoff envelope must be an object")
    return value


def claim_message(store: Any, project_id: str, message_id: str, *, keep: int = 1000) -> bool:
    """Atomically claim a Feishu message ID; fail closed without CAS support."""
    if not message_id or not hasattr(store, "compare_and_set"):
        raise GatewayError("durable compare_and_set state is required for event deduplication")
    key = "feishu_processed_message_ids"
    for _ in range(8):
        previous = store.get(project_id, key)
        values = list(previous or [])
        if message_id in values:
            return False
        if store.compare_and_set(project_id, key, previous, (values + [message_id])[-keep:]):
            return True
    raise GatewayError("could not claim message after concurrent updates")


def route_event(
    manifest: Mapping[str, Any], event: Mapping[str, Any], *, current_agent_id: str,
    store: Any, env: Mapping[str, str] = os.environ,
) -> RouteDecision:
    """Apply project isolation, explicit mention, typed handoff, and loop limits."""
    try:
        validate_manifest(manifest)
        by_id = {item["agent_id"]: item for item in _agents(manifest)}
        current = by_id.get(current_agent_id)
        if not current:
            raise GatewayError("current agent is not a member of this team")
        expected_chat = env.get(manifest["chat_id_env"], "")
        if not expected_chat or event.get("chat_id") != expected_chat:
            raise GatewayError("event belongs to another or unconfigured project chat")
        current_open_id = env.get(current["open_id_env"], "")
        if not current_open_id or current_open_id not in _mention_ids(event):
            raise GatewayError("current bot was not explicitly mentioned")
        message_id = str(event.get("message_id", ""))
        if not claim_message(store, manifest["project_id"], message_id):
            return RouteDecision(False, "duplicate_message")

        sender_type = event.get("sender_type")
        if sender_type == "user":
            if current_agent_id != manifest["secretary_agent_id"]:
                raise GatewayError("human requests enter through the secretary bot only")
            return RouteDecision(True, "human_to_secretary")
        if sender_type != "bot":
            raise GatewayError("unsupported sender type")

        open_to_agent = {env.get(item["open_id_env"], ""): item["agent_id"] for item in _agents(manifest)}
        sender = open_to_agent.get(str(event.get("sender_id", "")))
        if not sender:
            raise GatewayError("sender bot is not allowlisted for this team")
        envelope = _parse_envelope(str(event.get("content", "")))
        required = ("team_id", "project_id", "from", "to", "task", "correlation_id", "hop", "visited_edges")
        if any(key not in envelope for key in required):
            raise GatewayError("handoff envelope is incomplete")
        if envelope["team_id"] != manifest["team_id"] or envelope["project_id"] != manifest["project_id"]:
            raise GatewayError("handoff belongs to another team or project")
        if envelope["from"] != sender or envelope["to"] != current_agent_id:
            raise GatewayError("handoff sender or target does not match the event")
        hop = int(envelope["hop"])
        max_hops = int(manifest.get("policy", {}).get("max_hops", 6))
        if hop < 1 or hop > max_hops:
            raise GatewayError("handoff hop budget exceeded")
        edge = f"{sender}->{current_agent_id}"
        visited = list(envelope["visited_edges"])
        if not visited or visited[-1] != edge or visited.count(edge) != 1:
            raise GatewayError("handoff route contains a repeated or inconsistent edge")
        return RouteDecision(True, "bot_handoff", envelope)
    except (GatewayError, TypeError, ValueError) as exc:
        return RouteDecision(False, str(exc))
