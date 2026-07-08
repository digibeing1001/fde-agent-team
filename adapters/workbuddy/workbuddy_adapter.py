"""WorkBuddy runtime adapter for FDE resume/dispatch control.

WorkBuddy is a C-class single-agent host, so it cannot enforce a graph edge the
way LangGraph or Dify can. This adapter gives WorkBuddy a small mechanical
surface: user confirmation is converted into a StateGuard-committed transition,
then into a structured resume signal that the host can execute instead of
asking the LLM to describe the next step and stop.
"""

from __future__ import annotations

import time
from typing import Any

from adapters.state_guard import StateGuard, StateMachineError


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WorkBuddyResumeAdapter:
    """Bridge WorkBuddy user confirmations into FDE state-machine actions."""

    def __init__(self, guard: StateGuard, state_store):
        self.guard = guard
        self.state_store = state_store

    def confirm_and_resume(
        self,
        project_id: str,
        *,
        confirmed: bool,
        actor_id: str = "",
        next_state: str = "context",
        dispatch: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Commit the gate confirmation and write a resume signal.

        Args:
            project_id: FDE project id.
            confirmed: True when the user explicitly approved the next step.
            actor_id: Human approver id for auditability.
            next_state: State to resume into after a human gate.
            dispatch: Optional worker dispatch payload, e.g.
                {"agent": "research", "task": "..."}.
            reason: Human-readable confirmation reason.

        Returns:
            A structured resume signal persisted under ``resume_signal``.
        """
        current_state = self.guard.get_current_state(project_id)
        if current_state != "gate_phase":
            signal = self._blocked_signal(
                project_id,
                current_state=current_state,
                actor_id=actor_id,
                reason="resume requires current_state=gate_phase",
            )
            self.state_store.set(project_id, "resume_signal", signal)
            return signal

        if not confirmed:
            try:
                new_state = self.guard.commit_transition(
                    project_id,
                    "aborted",
                    produced_artifact="abort_reason.json",
                    user_confirmed=False,
                )
            except StateMachineError as exc:
                signal = self._blocked_signal(
                    project_id,
                    current_state=current_state,
                    actor_id=actor_id,
                    reason=str(exc),
                )
                self.state_store.set(project_id, "resume_signal", signal)
                return signal
            signal = {
                "version": "1.0.0",
                "kind": "fde-workbuddy-resume-signal",
                "project_id": project_id,
                "created_at": _now_iso(),
                "created_by": actor_id,
                "source": "workbuddy_user_confirmation",
                "control_decision": "cancel",
                "from_state": current_state,
                "to_state": new_state,
                "requires_dispatch": False,
                "next_action": {"type": "stop", "reason": reason or "user rejected"},
                "mechanical_enforcement_status": "state_guard_committed",
            }
            self.state_store.set(project_id, "resume_signal", signal)
            return signal

        new_state = self.guard.commit_transition(
            project_id,
            next_state,
            produced_artifact="user_confirmation.json",
            user_confirmed=True,
        )
        next_action = self._next_action(new_state, dispatch)
        signal = {
            "version": "1.0.0",
            "kind": "fde-workbuddy-resume-signal",
            "project_id": project_id,
            "created_at": _now_iso(),
            "created_by": actor_id,
            "source": "workbuddy_user_confirmation",
            "control_decision": "continue",
            "from_state": current_state,
            "to_state": new_state,
            "requires_dispatch": next_action["type"] == "dispatch_worker",
            "next_action": next_action,
            "forbidden_reply": "Do not answer with a plan or preparation note; execute next_action.",
            "reason": reason,
            "mechanical_enforcement_status": "state_guard_committed",
        }
        self.state_store.set(project_id, "resume_signal", signal)
        self.state_store.set(project_id, "last_user_confirmation", {
            "confirmed": True,
            "actor_id": actor_id,
            "created_at": signal["created_at"],
            "reason": reason,
        })
        return signal

    def next_workbuddy_payload(self, project_id: str) -> dict[str, Any]:
        """Return the minimal payload WorkBuddy should execute next."""
        signal = self.state_store.get(project_id, "resume_signal")
        if not signal:
            return {
                "version": "1.0.0",
                "kind": "fde-workbuddy-runtime-payload",
                "status": "blocked",
                "reason": "missing_resume_signal",
            }
        if signal.get("control_decision") != "continue":
            return {
                "version": "1.0.0",
                "kind": "fde-workbuddy-runtime-payload",
                "status": "blocked",
                "resume_signal": signal,
            }
        payload = {
            "version": "1.0.0",
            "kind": "fde-workbuddy-runtime-payload",
            "status": "ready",
            "project_id": project_id,
            "must_execute": signal["next_action"],
            "resume_signal": signal,
            "forbidden_reply": signal["forbidden_reply"],
        }
        self.state_store.set(project_id, "workbuddy_next_payload", payload)
        return payload

    def _next_action(self, state: str, dispatch: dict[str, Any] | None) -> dict[str, Any]:
        if dispatch:
            return {
                "type": "dispatch_worker",
                "agent": dispatch.get("agent", ""),
                "task": dispatch.get("task", ""),
                "tool": dispatch.get("tool") or self._tool_for_agent(str(dispatch.get("agent", ""))),
                "constraints": dispatch.get("constraints", []),
            }
        return {
            "type": "continue_loop",
            "state": state,
            "instruction": "resume the FDE loop immediately from this state",
        }

    @staticmethod
    def _tool_for_agent(agent: str) -> str:
        agent = agent.strip()
        if not agent:
            return "call_worker_agent"
        if agent.endswith("_agent"):
            return f"call_{agent}"
        return f"call_{agent}_agent"

    @staticmethod
    def _blocked_signal(project_id: str, *, current_state: str, actor_id: str, reason: str) -> dict[str, Any]:
        return {
            "version": "1.0.0",
            "kind": "fde-workbuddy-resume-signal",
            "project_id": project_id,
            "created_at": _now_iso(),
            "created_by": actor_id,
            "source": "workbuddy_user_confirmation",
            "control_decision": "wait_human",
            "from_state": current_state,
            "to_state": current_state,
            "requires_dispatch": False,
            "next_action": {"type": "wait", "reason": reason},
            "mechanical_enforcement_status": "blocked_by_state_guard",
        }
