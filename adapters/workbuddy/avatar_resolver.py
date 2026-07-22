"""Avatar resolver for WorkBuddy sub-agent display.

The FDE package declares each agent's avatar in two places:

* source of truth : ``team.yaml``
  (``secretary.avatar`` and ``agents.<key>.avatar``)
* built WorkBuddy plugin : ``.codebuddy-plugin/plugin.json``
  (``members[].avatar``)

WorkBuddy's runtime, when it SPAWNS a sub-agent (Agent tool / TeamCreate
spawn), currently does NOT copy the agent's avatar into the teammate's display
metadata. Empirically verified: the team config written by the harness for a
spawned teammate contains ``name/role/agentType/color/backendType`` but **no
``avatar`` field**, even though the avatar is present in both ``plugin.json``
members[] and the agent ``.md`` frontmatter. This is upstream defect
**WB-HARNESS-P0-001**.

This module is the *landing point* for that harness fix. The host should call
:func:`resolve_avatar` when it registers a spawned teammate and attach the
returned path to the teammate's UI record, so the avatar renders like it does
for the top-level expert.

Accepted identifiers: team.yaml keys (``echo``, ``delta``, ...), built plugin
ids (``echo-analyst``, ...), and the secretary id (``fde-lead`` /
``fde-agent-team-team-lead``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

# team.yaml key -> built plugin id
_TEAM_KEY_TO_PLUGIN_ID = {
    "fde-lead": "fde-agent-team-team-lead",
    "echo": "echo-analyst",
    "delta": "delta-engineer",
    "productize": "productize-specialist",
    "research": "research-analyst",
    "knowledge-curator": "knowledge-curator",
    "qa": "qa-gatekeeper",
    "legal": "legal-reviewer",
    "coach": "growth-coach",
}
_PLUGIN_ID_TO_TEAM_KEY = {v: k for k, v in _TEAM_KEY_TO_PLUGIN_ID.items()}

_AGENT_KEY_RE = re.compile(
    r"^\s*(echo|delta|productize|research|knowledge-curator|qa|legal|coach):\s*$"
)
_ROLE_CARD_RE = re.compile(r"^\s*role_card:\s*agents/([\w-]+)/SKILL\.md")
_AVATAR_RE = re.compile(r"^\s*avatar:\s*(\S+)")


def _repo_root(start: str = __file__) -> str:
    # adapters/workbuddy/avatar_resolver.py -> repo root
    p = os.path.dirname(os.path.abspath(start))
    return os.path.dirname(os.path.dirname(p))


def _load_plugin_map(repo_root: str) -> dict[str, str]:
    """Read .codebuddy-plugin/plugin.json members[].avatar -> {id: avatar}."""
    candidates = [
        os.path.join(repo_root, ".codebuddy-plugin", "plugin.json"),
        os.path.join(repo_root, "plugin.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        result: dict[str, str] = {}
        for m in data.get("members", []) or []:
            aid = m.get("id")
            av = m.get("avatar")
            if aid and av:
                result[aid] = av
        top = data.get("avatar")
        if top:
            result["__team__"] = top
        return result
    return {}


def _load_team_yaml_map(repo_root: str) -> dict[str, str]:
    """Fallback parser: read avatar from team.yaml without a hard YAML dep.

    Registers the avatar under three equivalent identifiers so callers may pass
    any of them: team.yaml key (``echo``), agent dir (``echo-agent``), or built
    plugin id (``echo-analyst``).
    """
    path = os.path.join(repo_root, "team.yaml")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    cur_team_key: Optional[str] = None
    cur_dir: Optional[str] = None
    for line in lines:
        s = line.rstrip("\n")
        if "secretary:" in s:
            cur_team_key, cur_dir = "fde-lead", "fde-lead"
            continue
        m = _AGENT_KEY_RE.match(s)
        if m:
            cur_team_key = m.group(1)  # keep cur_dir (set by role_card later)
            continue
        rm = _ROLE_CARD_RE.match(s)
        if rm:
            cur_dir = rm.group(1)
            continue
        am = _AVATAR_RE.match(s)
        if am and (cur_team_key or cur_dir):
            av = am.group(1)
            for key in (cur_team_key, cur_dir):
                if key:
                    result[key] = av
            if cur_team_key and cur_team_key in _TEAM_KEY_TO_PLUGIN_ID:
                result[_TEAM_KEY_TO_PLUGIN_ID[cur_team_key]] = av
            cur_team_key, cur_dir = None, None
    return result


def resolve_avatar(agent_id: str, repo_root: Optional[str] = None) -> Optional[str]:
    """Return the avatar path for an agent id (team.yaml key OR plugin id).

    Returns ``None`` if unknown. The host should attach the result to the
    spawned teammate's display metadata so the avatar renders in the UI.
    """
    if not agent_id:
        return None
    repo_root = repo_root or _repo_root()
    plugin_map = _load_plugin_map(repo_root)
    team_map = _load_team_yaml_map(repo_root)

    candidates = [agent_id]
    if agent_id in _TEAM_KEY_TO_PLUGIN_ID:
        candidates.append(_TEAM_KEY_TO_PLUGIN_ID[agent_id])
    if agent_id in _PLUGIN_ID_TO_TEAM_KEY:
        candidates.append(_PLUGIN_ID_TO_TEAM_KEY[agent_id])

    for cid in candidates:
        if cid in plugin_map:
            return plugin_map[cid]
    for cid in candidates:
        if cid in team_map:
            return team_map[cid]
    return None


def team_avatar(repo_root: Optional[str] = None) -> Optional[str]:
    """Return the team-level avatar (plugin.json top-level ``avatar``)."""
    repo_root = repo_root or _repo_root()
    return _load_plugin_map(repo_root).get("__team__")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: avatar_resolver.py <agent_id>", file=sys.stderr)
        raise SystemExit(2)
    out = resolve_avatar(sys.argv[1])
    print(out or "")
