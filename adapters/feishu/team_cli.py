"""One-command Feishu provisioning for an independent FDE agent team.

Only the secretary is installed as a Feishu bot. Worker roles are independent
runtime actors and are registered in the project-group roster; their signed
messages travel through the secretary bot so the group never needs eight extra
Feishu applications.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    from adapters.agent_team_runtime import AgentTeamRuntime, DEFAULT_ROLES
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from adapters.agent_team_runtime import AgentTeamRuntime, DEFAULT_ROLES


FDE_PROJECT_TYPE = "fde_ai_consulting"
INTAKE_QUESTIONS = (
    ("business_outcome", "如果这个 FDE 项目只能达成一个业务结果，最重要的结果是什么？"),
    ("users_and_workflow", "谁会在什么实际工作流程中使用这套方案？"),
    ("success_measure", "用什么可验证指标判断项目已经成功？"),
    ("constraints", "当前最不可妥协的技术、数据、安全或时间约束是什么？"),
    ("available_evidence", "目前有哪些系统、数据、访谈对象或现有材料可供团队验证假设？"),
)


class FeishuCLIError(RuntimeError):
    """Raised when lark-cli returns an invalid or unsuccessful response."""


class JsonFileStateStore:
    """Small persistent StateStore used by the standalone CLI."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get(self, project_id: str, key: str) -> Any:
        data = self._read()
        return copy.deepcopy(data.get(project_id, {}).get(key))

    def set(self, project_id: str, key: str, value: Any) -> None:
        data = self._read()
        data.setdefault(project_id, {})[key] = copy.deepcopy(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))


class LarkCLI:
    """Strict JSON wrapper around the official ``lark-cli`` executable."""

    def __init__(
        self,
        executable: str = "lark-cli",
        executor: Optional[Callable[[Sequence[str]], Any]] = None,
    ):
        self.executable = executable
        self.executor = executor or self._execute

    def call(self, arguments: Sequence[str]) -> dict[str, Any]:
        command = [self.executable, *arguments]
        result = self.executor(command)
        if isinstance(result, Mapping):
            payload = dict(result)
        else:
            raw = result.stdout if hasattr(result, "stdout") else str(result)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FeishuCLIError(f"lark-cli did not return JSON: {raw}") from exc
        if payload.get("ok") is False:
            raise FeishuCLIError(str(payload.get("error") or payload))
        return payload

    @staticmethod
    def _execute(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )


class SocraticIntake:
    """FDE-only discovery gate that emits at most one question per turn."""

    storage_key = "socratic_intake_v1"

    def __init__(self, state_store: Any):
        self.state_store = state_store

    def begin(
        self, project_id: str, initial_context: Optional[Mapping[str, Any]] = None
    ) -> dict[str, Any]:
        context = copy.deepcopy(dict(initial_context or {}))
        project_type = context.get("project_type", FDE_PROJECT_TYPE)
        if project_type != FDE_PROJECT_TYPE:
            raise ValueError("仅支持 FDE 类型的 AI 咨询项目")
        context["project_type"] = project_type
        state = {
            "project_id": project_id,
            "context": context,
            "status": "collecting",
            "question_history": [],
        }
        self._persist(state)
        return self.next_turn(project_id)

    def answer(self, project_id: str, field: str, answer: str) -> dict[str, Any]:
        state = self._get(project_id)
        outstanding = state.get("outstanding_question")
        if not outstanding or outstanding["field"] != field:
            raise ValueError("answer field must match the outstanding question")
        if not answer.strip():
            raise ValueError("answer cannot be empty")
        state["context"][field] = answer.strip()
        state["outstanding_question"] = None
        self._persist(state)
        return self.next_turn(project_id)

    def next_turn(self, project_id: str) -> dict[str, Any]:
        state = self._get(project_id)
        outstanding = state.get("outstanding_question")
        if outstanding:
            return {"ready": False, "question": copy.deepcopy(outstanding)}
        for field, question in INTAKE_QUESTIONS:
            if not str(state["context"].get(field, "")).strip():
                item = {"field": field, "text": question}
                state["outstanding_question"] = item
                state["question_history"].append(copy.deepcopy(item))
                self._persist(state)
                return {"ready": False, "question": item}
        state["status"] = "ready"
        state["outstanding_question"] = None
        self._persist(state)
        return {"ready": True, "context": copy.deepcopy(state["context"])}

    def _get(self, project_id: str) -> dict[str, Any]:
        state = self.state_store.get(project_id, self.storage_key)
        if state is None:
            raise ValueError(f"intake not found for project {project_id}")
        return state

    def _persist(self, state: dict[str, Any]) -> None:
        self.state_store.set(state["project_id"], self.storage_key, state)


class FeishuTeamProvisioner:
    """Create a Feishu project group and an independent runtime agent team."""

    install_key = "feishu_team_install_v1"
    project_key = "feishu_project_v1"

    def __init__(
        self,
        cli: LarkCLI,
        state_store: Any,
        team_runtime: Optional[AgentTeamRuntime] = None,
        execution_starter: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
    ):
        self.cli = cli
        self.state_store = state_store
        self.team_runtime = team_runtime or AgentTeamRuntime(state_store)
        self.intake = SocraticIntake(state_store)
        self.execution_starter = execution_starter

    def import_team(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """Validate auth and persist a one-bot installation manifest."""
        roles = tuple(config.get("roles") or DEFAULT_ROLES)
        bot_role = config.get("visible_bot_role", "fde-lead")
        if bot_role != "fde-lead":
            raise ValueError("visible_bot_role must be fde-lead")
        if config.get("worker_bot_app_ids"):
            raise ValueError("worker agents must not be installed as extra Feishu bots")
        auth = self.cli.call(["auth", "status", "--json", "--verify"])
        manifest = {
            "installed": True,
            "visible_bot_count": 1,
            "visible_bot_role": bot_role,
            "independent_agent_roles": list(roles),
            "auth_identity": auth.get("identity"),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.state_store.set("__installation__", self.install_key, manifest)
        return copy.deepcopy(manifest)

    def start_project(
        self,
        project_id: str,
        project_name: str,
        owner_open_id: str,
        member_open_ids: Sequence[str] = (),
        initial_context: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """One call: group, human members, independent agents, roster, intake."""
        if self.state_store.get("__installation__", self.install_key) is None:
            raise ValueError("run import-team before starting a project")
        if self.state_store.get(project_id, self.project_key) is not None:
            raise ValueError(f"project {project_id} already exists")
        create = self.cli.call(
            [
                "im", "+chat-create", "--name", project_name,
                "--users", owner_open_id, "--as", "bot",
                "--set-bot-manager", "--format", "json",
            ]
        )
        chat_id = self._extract_chat_id(create)
        additional = [item for item in dict.fromkeys(member_open_ids) if item != owner_open_id]
        if additional:
            add_result = self.cli.call(
                [
                    "im", "chat.members", "create", "--params",
                    json.dumps(
                        {
                            "chat_id": chat_id,
                            "member_id_type": "open_id",
                            "succeed_type": 1,
                        },
                        separators=(",", ":"),
                    ),
                    "--data", json.dumps({"id_list": additional}, separators=(",", ":")),
                    "--as", "user", "--format", "json",
                ]
            )
            invalid = self._data(add_result).get("invalid_id_list", [])
            if invalid:
                raise FeishuCLIError(f"failed to add project members: {invalid}")

        team = self.team_runtime.provision(project_id, chat_id)
        self._send_markdown(chat_id, self.team_runtime.roster_markdown(project_id))
        intake_turn = self.intake.begin(project_id, initial_context)
        project = {
            "project_id": project_id,
            "project_name": project_name,
            "chat_id": chat_id,
            "owner_open_id": owner_open_id,
            "human_member_open_ids": [owner_open_id, *additional],
            "agent_instance_ids": [agent["instance_id"] for agent in team["agents"]],
            "status": "intake" if not intake_turn["ready"] else "active",
        }
        if intake_turn["ready"]:
            self._activate_and_announce(
                project_id, chat_id, intake_turn["context"]
            )
        else:
            self._send_markdown(chat_id, f"**统筹官提问（本轮仅一题）**\n\n{intake_turn['question']['text']}")
        self.state_store.set(project_id, self.project_key, project)
        return {"project": project, "team": team, "intake": intake_turn}

    def answer_intake(self, project_id: str, field: str, answer: str) -> dict[str, Any]:
        project = self.state_store.get(project_id, self.project_key)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        turn = self.intake.answer(project_id, field, answer)
        if turn["ready"]:
            team = self._activate_and_announce(
                project_id, project["chat_id"], turn["context"]
            )
            project["status"] = "active"
            self.state_store.set(project_id, self.project_key, project)
            return {"project": project, "team": team, "intake": turn}
        self._send_markdown(
            project["chat_id"],
            f"**统筹官提问（本轮仅一题）**\n\n{turn['question']['text']}",
        )
        return {"project": project, "intake": turn}

    def relay_agent_message(
        self, project_id: str, role: str, content: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Publish one worker's signed output through the shared secretary bot."""
        project = self.state_store.get(project_id, self.project_key)
        if project is None:
            raise ValueError(f"project {project_id} not found")
        envelope = self.team_runtime.publish(project_id, role, content)
        text = str(content.get("text") or json.dumps(content, ensure_ascii=False))
        markdown = (
            f"### [{role}]\n\n{text}\n\n"
            f"`agent_instance_id: {envelope['agent_instance_id']}`"
        )
        self._send_markdown(project["chat_id"], markdown)
        return envelope

    def _activate_and_announce(
        self, project_id: str, chat_id: str, context: Mapping[str, Any]
    ) -> dict[str, Any]:
        team = self.team_runtime.activate(project_id)
        self._send_markdown(
            chat_id,
            "## 上下文收集完成\n\n统筹官已激活全部独立 Agent，开始拆解任务、规划路径并执行。",
        )
        if self.execution_starter is not None:
            self.execution_starter(project_id, copy.deepcopy(dict(context)))
        return team

    def _send_markdown(self, chat_id: str, markdown: str) -> dict[str, Any]:
        return self.cli.call(
            [
                "im", "+messages-send", "--as", "bot", "--chat-id", chat_id,
                "--markdown", markdown, "--format", "json",
            ]
        )

    @staticmethod
    def _data(payload: Mapping[str, Any]) -> dict[str, Any]:
        data = payload.get("data", payload)
        return dict(data) if isinstance(data, Mapping) else {}

    @classmethod
    def _extract_chat_id(cls, payload: Mapping[str, Any]) -> str:
        data = cls._data(payload)
        chat_id = data.get("chat_id")
        if not chat_id and isinstance(data.get("data"), Mapping):
            chat_id = data["data"].get("chat_id")
        if not chat_id:
            raise FeishuCLIError(f"chat-create response has no chat_id: {payload}")
        return str(chat_id)


def _load_config(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fde-feishu", description="FDE 飞书团队一键导入与项目启动")
    parser.add_argument("--state", default=".fde/feishu-team-state.json")
    parser.add_argument("--lark-cli", default="lark-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-team", help="导入整支团队（飞书仅一个统筹官机器人）")
    import_parser.add_argument("--config", required=True)

    start = subparsers.add_parser("start-project", help="创建项目群、Agent 团队并启动引导")
    start.add_argument("--project-id")
    start.add_argument("--name", required=True)
    start.add_argument("--owner-open-id", required=True)
    start.add_argument("--member-open-id", action="append", default=[])
    start.add_argument("--context-json", default="{}")

    bootstrap = subparsers.add_parser(
        "bootstrap", help="一条命令完成团队导入、建群、Agent 创建与启动引导"
    )
    bootstrap.add_argument("--config", required=True)
    bootstrap.add_argument("--project-id")
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--owner-open-id", required=True)
    bootstrap.add_argument("--member-open-id", action="append", default=[])
    bootstrap.add_argument("--context-json", default="{}")

    answer = subparsers.add_parser("answer", help="回答当前唯一的苏格拉底式问题")
    answer.add_argument("--project-id", required=True)
    answer.add_argument("--field", required=True)
    answer.add_argument("--text", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    state = JsonFileStateStore(args.state)
    provisioner = FeishuTeamProvisioner(LarkCLI(args.lark_cli), state)
    if args.command == "import-team":
        result = provisioner.import_team(_load_config(args.config))
    elif args.command in {"start-project", "bootstrap"}:
        if args.command == "bootstrap":
            provisioner.import_team(_load_config(args.config))
        project_id = args.project_id or uuid.uuid4().hex
        result = provisioner.start_project(
            project_id,
            args.name,
            args.owner_open_id,
            args.member_open_id,
            json.loads(args.context_json),
        )
    else:
        result = provisioner.answer_intake(args.project_id, args.field, args.text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FDE_PROJECT_TYPE",
    "FeishuCLIError",
    "FeishuTeamProvisioner",
    "JsonFileStateStore",
    "LarkCLI",
    "SocraticIntake",
    "build_parser",
    "main",
]
