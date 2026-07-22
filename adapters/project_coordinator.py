"""Bridge independent agent instances to the scored FDE execution loop."""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Optional

from adapters.agent_team_runtime import AgentTeamRuntime
from adapters.loop_orchestrator import LoopOrchestrator


class ProjectCoordinator:
    """Let the secretary plan, dispatch workers and enforce Lead score gates.

    The callbacks are intentionally explicit integration seams. A host can use
    any model/runtime, while this layer guarantees that worker output originates
    from the assigned independent agent and cannot skip the Lead score gate.
    """

    def __init__(
        self,
        team_runtime: AgentTeamRuntime,
        loop: LoopOrchestrator,
        planner: Callable[[Mapping[str, Any], list[str]], Mapping[str, Any]],
        worker_executor: Callable[[str, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        lead_scorer: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        event_sink: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
    ):
        self.team_runtime = team_runtime
        self.loop = loop
        self.planner = planner
        self.worker_executor = worker_executor
        self.lead_scorer = lead_scorer
        self.event_sink = event_sink or (lambda _role, _payload: None)

    def start(
        self, project_id: str, context: Mapping[str, Any], max_cycles: int = 100
    ) -> dict[str, Any]:
        """Plan dynamically, dispatch every ready node and run until a hard gate."""
        team = self.team_runtime.get(project_id)
        if team["status"] != "active":
            raise ValueError("independent agent team must be active")
        roles = [agent["role"] for agent in team["agents"]]
        plan = dict(self.planner(copy.deepcopy(dict(context)), roles))
        objective = str(plan.get("objective", "")).strip()
        steps = plan.get("steps")
        if not objective or not isinstance(steps, list):
            raise ValueError("planner must return objective and steps")
        selected = {str(step.get("agent")) for step in steps}
        unavailable = sorted(selected - set(roles))
        if unavailable:
            raise ValueError(f"planner selected unavailable agents: {unavailable}")
        self.loop.create_run(project_id, objective, steps, context)
        self.event_sink("fde-lead", {"event": "plan_created", "steps": steps})
        return self.resume(project_id, context, max_cycles=max_cycles)

    def resume(
        self, project_id: str, context: Mapping[str, Any], max_cycles: int = 100
    ) -> dict[str, Any]:
        cycles = 0
        while cycles < max_cycles:
            run = self.loop.get_run(project_id)
            if run["status"] in {"blocked", "awaiting_user_acceptance", "accepted"}:
                return run
            candidates = [
                step for step in run["steps"] if step["status"] in {"ready", "rework"}
            ]
            if not candidates:
                raise RuntimeError("running workflow has no executable step")
            for snapshot in candidates:
                cycles += 1
                if cycles > max_cycles:
                    break
                step = self.loop.start_step(project_id, snapshot["step_id"])
                task = {
                    "step_id": step["step_id"],
                    "description": step["task_description"],
                    "expected_outputs": step["expected_outputs"],
                    "pass_conditions": step["pass_conditions"],
                    "lead_feedback": step["last_feedback"],
                }
                self.team_runtime.dispatch(project_id, step["agent"], task)
                outputs = dict(
                    self.worker_executor(
                        step["agent"], copy.deepcopy(task), copy.deepcopy(dict(context))
                    )
                )
                self.team_runtime.publish(project_id, step["agent"], outputs)
                evidence_refs = list(outputs.pop("_evidence_refs", []))
                self.loop.submit_step_result(
                    project_id, step["step_id"], outputs, evidence_refs
                )
                scored_step = self.loop.get_run(project_id)
                scored_snapshot = next(
                    item for item in scored_step["steps"] if item["step_id"] == step["step_id"]
                )
                review = dict(
                    self.lead_scorer(
                        copy.deepcopy(scored_snapshot),
                        copy.deepcopy(outputs),
                        copy.deepcopy(dict(context)),
                    )
                )
                result = self.loop.score_step(
                    project_id,
                    step["step_id"],
                    review["rubric_scores"],
                    str(review.get("feedback", "")),
                    scorer="fde-lead",
                )
                self.event_sink(
                    "fde-lead",
                    {
                        "event": "step_scored",
                        "step_id": step["step_id"],
                        "agent": step["agent"],
                        "score": result["score"],
                        "threshold": result["threshold"],
                        "passed": result["passed"],
                    },
                )
                if result["run_status"] == "blocked":
                    return self.loop.get_run(project_id)
        raise RuntimeError(f"automatic execution exceeded {max_cycles} cycles")


__all__ = ["ProjectCoordinator"]
