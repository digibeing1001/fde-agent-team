"""Deterministic loop runtime for the FDE Agent Team.

The Lead still decides *what* work should be done and delegates all production
work.  This module only enforces the hard invariants around that work:

* validate a dependency-aware execution plan and derive the active team;
* run one step at a time after its dependencies pass;
* calculate a weighted Lead score after every submitted result;
* return failed work to the same node until it passes or needs escalation;
* wait for explicit user acceptance after all steps pass;
* invalidate the responsible node and its downstream work after rejection;
* persist every transition, score, rework and acceptance decision.

It intentionally uses only the standard library and the existing StateStore
contract (``get(project_id, key)`` / ``set(project_id, key, value)``).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional


class LoopWorkflowError(Exception):
    """Raised when a caller attempts an invalid workflow operation."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"[FDELoop] {code}: {detail}")


DEFAULT_AGENTS = {
    "echo",
    "delta",
    "productize",
    "research",
    "knowledge-curator",
    "qa",
    "legal",
    "coach",
}

DEFAULT_POLICY = {
    "version": "2.2",
    "score_thresholds": {"low": 75.0, "medium": 80.0, "high": 90.0},
    "max_reworks_per_step": 3,
    "rubric": {
        "goal_alignment": 0.25,
        "completeness": 0.20,
        "evidence": 0.15,
        "execution_quality": 0.20,
        "safety_compliance": 0.10,
        "user_constraints": 0.10,
    },
}

STEP_STATES = {
    "pending",
    "ready",
    "running",
    "scoring",
    "rework",
    "passed",
    "blocked",
}

RUN_STATES = {
    "planned",
    "running",
    "blocked",
    "awaiting_user_acceptance",
    "accepted",
}


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class LoopOrchestrator:
    """Programmatic enforcement layer for the FDE execution/feedback loop."""

    storage_key = "loop_run_v22"

    def __init__(
        self,
        state_store: Any,
        policy: Optional[Mapping[str, Any]] = None,
        agent_registry: Optional[Iterable[str]] = None,
        clock: Optional[Callable[[], str]] = None,
    ):
        self.state_store = state_store
        self.policy = self._normalize_policy(policy or DEFAULT_POLICY)
        self.agent_registry = set(agent_registry or DEFAULT_AGENTS)
        self.clock = clock or _utc_timestamp

    @classmethod
    def from_policy_file(
        cls,
        state_store: Any,
        policy_path: str | Path,
        agent_registry: Optional[Iterable[str]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> "LoopOrchestrator":
        with open(policy_path, "r", encoding="utf-8") as handle:
            policy = json.load(handle)
        return cls(state_store, policy, agent_registry, clock)

    def create_run(
        self,
        project_id: str,
        objective: str,
        steps: list[Mapping[str, Any]],
        user_constraints: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Validate the Lead plan, derive the team, and persist a new run."""
        if not project_id.strip():
            raise LoopWorkflowError("invalid_project_id", "project_id 不能为空")
        if not objective.strip():
            raise LoopWorkflowError("invalid_objective", "objective 不能为空")
        if self.state_store.get(project_id, self.storage_key) is not None:
            raise LoopWorkflowError("run_exists", f"项目 {project_id} 已存在 loop run")

        normalized_steps = self._validate_and_normalize_steps(steps)
        run = {
            "schema_version": "2.2",
            "project_id": project_id,
            "objective": objective,
            "status": "planned",
            "user_constraints": copy.deepcopy(dict(user_constraints or {})),
            "team_manifest": self._build_team_manifest(normalized_steps),
            "steps": normalized_steps,
            "delivery_bundle": None,
            "user_feedback_history": [],
            "retrospective_queue": [],
            "event_log": [],
            "created_at": self.clock(),
            "updated_at": self.clock(),
        }
        self._event(run, "run_created", team=run["team_manifest"])
        self._refresh_ready_steps(run)
        run["status"] = "running"
        self._persist(run)
        return copy.deepcopy(run)

    def get_run(self, project_id: str) -> dict[str, Any]:
        run = self.state_store.get(project_id, self.storage_key)
        if run is None:
            raise LoopWorkflowError("run_not_found", f"项目 {project_id} 没有 loop run")
        return copy.deepcopy(run)

    def start_step(self, project_id: str, step_id: str) -> dict[str, Any]:
        run = self.get_run(project_id)
        self._require_run_status(run, {"running"})
        step = self._step(run, step_id)
        if step["status"] not in {"ready", "rework"}:
            raise LoopWorkflowError(
                "step_not_startable",
                f"步骤 {step_id} 当前状态为 {step['status']}，必须为 ready/rework",
            )
        missing = [
            dep
            for dep in step["depends_on"]
            if self._step(run, dep)["status"] != "passed"
        ]
        if missing:
            raise LoopWorkflowError(
                "dependency_not_passed", f"步骤 {step_id} 的依赖尚未通过: {missing}"
            )
        step["status"] = "running"
        step["attempt_count"] += 1
        step["started_at"] = self.clock()
        self._event(
            run,
            "step_started",
            step_id=step_id,
            agent=step["agent"],
            attempt=step["attempt_count"],
        )
        self._persist(run)
        return copy.deepcopy(step)

    def submit_step_result(
        self,
        project_id: str,
        step_id: str,
        outputs: Mapping[str, Any],
        evidence_refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Store a worker result and move it to the Lead scoring gate."""
        run = self.get_run(project_id)
        self._require_run_status(run, {"running"})
        step = self._step(run, step_id)
        if step["status"] != "running":
            raise LoopWorkflowError(
                "step_not_running", f"步骤 {step_id} 当前状态为 {step['status']}"
            )
        if not outputs:
            raise LoopWorkflowError("missing_outputs", f"步骤 {step_id} 没有提交产出")
        missing_outputs = [
            name for name in step["expected_outputs"] if name not in outputs
        ]
        if missing_outputs:
            raise LoopWorkflowError(
                "expected_outputs_missing",
                f"步骤 {step_id} 缺少预期产出: {missing_outputs}",
            )

        if step["outputs"]:
            step["output_history"].append(copy.deepcopy(step["outputs"]))
        step["outputs"] = copy.deepcopy(dict(outputs))
        step["evidence_refs"] = list(evidence_refs or [])
        step["status"] = "scoring"
        self._event(
            run,
            "step_submitted",
            step_id=step_id,
            output_names=sorted(outputs.keys()),
            evidence_count=len(step["evidence_refs"]),
        )
        self._persist(run)
        return copy.deepcopy(step)

    def calculate_score(self, rubric_scores: Mapping[str, float]) -> float:
        """Calculate the configured weighted score and reject partial rubrics."""
        rubric = self.policy["rubric"]
        missing = sorted(set(rubric) - set(rubric_scores))
        unknown = sorted(set(rubric_scores) - set(rubric))
        if missing or unknown:
            raise LoopWorkflowError(
                "invalid_score_dimensions",
                f"缺失维度={missing}，未知维度={unknown}",
            )
        weighted = 0.0
        for dimension, weight in rubric.items():
            value = float(rubric_scores[dimension])
            if value < 0 or value > 100:
                raise LoopWorkflowError(
                    "score_out_of_range", f"{dimension}={value} 不在 0..100"
                )
            weighted += value * weight
        return round(weighted, 2)

    def score_step(
        self,
        project_id: str,
        step_id: str,
        rubric_scores: Mapping[str, float],
        feedback: str,
        scorer: str = "fde-lead",
    ) -> dict[str, Any]:
        """Run the mandatory Lead score gate and route pass/rework/block."""
        if scorer != "fde-lead":
            raise LoopWorkflowError(
                "invalid_scorer", "逐步评分必须由 fde-lead 提交"
            )
        run = self.get_run(project_id)
        self._require_run_status(run, {"running"})
        step = self._step(run, step_id)
        if step["status"] != "scoring":
            raise LoopWorkflowError(
                "step_not_scoring", f"步骤 {step_id} 当前状态为 {step['status']}"
            )

        score = self.calculate_score(rubric_scores)
        threshold = step["pass_score"]
        passed = score >= threshold
        score_record = {
            "attempt": step["attempt_count"],
            "scorer": scorer,
            "rubric_scores": {key: float(value) for key, value in rubric_scores.items()},
            "weighted_score": score,
            "pass_score": threshold,
            "passed": passed,
            "feedback": feedback,
            "timestamp": self.clock(),
        }
        step["score_history"].append(score_record)
        step["last_score"] = score
        step["last_feedback"] = feedback

        if passed:
            step["status"] = "passed"
            step["final_pass"] = True
            step["finished_at"] = self.clock()
            self._event(
                run,
                "step_passed",
                step_id=step_id,
                score=score,
                threshold=threshold,
            )
            self._refresh_ready_steps(run)
            if all(item["status"] == "passed" for item in run["steps"]):
                run["status"] = "awaiting_user_acceptance"
                run["delivery_bundle"] = self._build_delivery_bundle(run)
                self._event(run, "delivery_ready", step_count=len(run["steps"]))
        else:
            step["rework_count"] += 1
            step["final_pass"] = False
            max_reworks = step["max_reworks"]
            if step["rework_count"] > max_reworks:
                step["status"] = "blocked"
                run["status"] = "blocked"
                self._event(
                    run,
                    "step_blocked",
                    step_id=step_id,
                    score=score,
                    threshold=threshold,
                    rework_count=step["rework_count"],
                )
            else:
                step["status"] = "rework"
                self._event(
                    run,
                    "step_rework_requested",
                    step_id=step_id,
                    score=score,
                    threshold=threshold,
                    rework_count=step["rework_count"],
                )

        self._persist(run)
        return {
            "step": copy.deepcopy(step),
            "run_status": run["status"],
            "score": score,
            "threshold": threshold,
            "passed": passed,
        }

    def extend_rework_budget(
        self, project_id: str, step_id: str, additional_attempts: int
    ) -> dict[str, Any]:
        """Resume an escalated node after an explicit human budget extension."""
        if additional_attempts <= 0:
            raise LoopWorkflowError("invalid_extension", "additional_attempts 必须 > 0")
        run = self.get_run(project_id)
        step = self._step(run, step_id)
        if run["status"] != "blocked" or step["status"] != "blocked":
            raise LoopWorkflowError("run_not_blocked", "只有 blocked 步骤可扩展返工预算")
        step["max_reworks"] += additional_attempts
        step["status"] = "rework"
        run["status"] = "running"
        self._event(
            run,
            "rework_budget_extended",
            step_id=step_id,
            additional_attempts=additional_attempts,
            new_max=step["max_reworks"],
        )
        self._persist(run)
        return copy.deepcopy(step)

    def record_user_feedback(
        self,
        project_id: str,
        accepted: bool,
        feedback: str,
        root_cause_step_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Close the loop or return rejected work to the diagnosed node(s)."""
        run = self.get_run(project_id)
        self._require_run_status(run, {"awaiting_user_acceptance"})
        feedback_record = {
            "accepted": bool(accepted),
            "feedback": feedback,
            "root_cause_step_ids": list(root_cause_step_ids or []),
            "timestamp": self.clock(),
        }
        run["user_feedback_history"].append(feedback_record)

        if accepted:
            run["status"] = "accepted"
            run["retrospective_queue"] = [
                {
                    "step_id": step["step_id"],
                    "agent": step["agent"],
                    "failed_attempts": sum(
                        1 for score in step["score_history"] if not score["passed"]
                    ),
                    "score_history": [
                        score["weighted_score"] for score in step["score_history"]
                    ],
                    "improvement_focus": step["last_feedback"],
                }
                for step in run["steps"]
                if step["rework_count"] > 0
            ]
            self._event(
                run,
                "user_accepted",
                retrospective_items=len(run["retrospective_queue"]),
            )
        else:
            root_ids = list(root_cause_step_ids or [])
            if not root_ids:
                raise LoopWorkflowError(
                    "missing_root_cause",
                    "用户不满意时，FDE Lead 必须定位并提交 root_cause_step_ids",
                )
            for step_id in root_ids:
                self._step(run, step_id)
            invalidated = self._invalidate_from_roots(run, root_ids)
            run["status"] = "running"
            run["delivery_bundle"] = None
            self._refresh_ready_steps(run)
            self._event(
                run,
                "user_rejected",
                root_cause_step_ids=root_ids,
                invalidated_step_ids=invalidated,
                feedback=feedback,
            )

        self._persist(run)
        return copy.deepcopy(run)

    def suggest_rework_candidates(self, project_id: str) -> list[dict[str, Any]]:
        """Give the Lead evidence for diagnosis; never silently choose a node."""
        run = self.get_run(project_id)
        ranked = sorted(
            run["steps"],
            key=lambda step: (
                step["last_score"] if step["last_score"] is not None else 101,
                -step["rework_count"],
                step["step_order"],
            ),
        )
        return [
            {
                "step_id": step["step_id"],
                "agent": step["agent"],
                "last_score": step["last_score"],
                "rework_count": step["rework_count"],
                "last_feedback": step["last_feedback"],
            }
            for step in ranked
        ]

    def audit_report(self, project_id: str) -> dict[str, Any]:
        """Return the complete trace required for review and improvement."""
        run = self.get_run(project_id)
        return {
            "project_id": project_id,
            "objective": run["objective"],
            "run_status": run["status"],
            "team_manifest": copy.deepcopy(run["team_manifest"]),
            "steps": [
                {
                    "step_id": step["step_id"],
                    "agent": step["agent"],
                    "status": step["status"],
                    "last_score": step["last_score"],
                    "score_history": copy.deepcopy(step["score_history"]),
                    "attempt_count": step["attempt_count"],
                    "rework_count": step["rework_count"],
                    "invalidation_count": step["invalidation_count"],
                    "final_pass": step["final_pass"],
                }
                for step in run["steps"]
            ],
            "user_feedback_history": copy.deepcopy(run["user_feedback_history"]),
            "retrospective_queue": copy.deepcopy(run["retrospective_queue"]),
            "event_log": copy.deepcopy(run["event_log"]),
            "updated_at": run["updated_at"],
        }

    def _normalize_policy(self, policy: Mapping[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(DEFAULT_POLICY)
        for key, value in policy.items():
            merged[key] = copy.deepcopy(value)
        rubric = merged.get("rubric", {})
        if not rubric:
            raise LoopWorkflowError("invalid_policy", "rubric 不能为空")
        weight_sum = sum(float(value) for value in rubric.values())
        if abs(weight_sum - 1.0) > 1e-9:
            raise LoopWorkflowError("invalid_policy", f"rubric 权重总和必须为 1，当前 {weight_sum}")
        for risk in ("low", "medium", "high"):
            threshold = float(merged["score_thresholds"][risk])
            if threshold < 0 or threshold > 100:
                raise LoopWorkflowError("invalid_policy", f"{risk} 阈值不在 0..100")
        if int(merged["max_reworks_per_step"]) < 0:
            raise LoopWorkflowError("invalid_policy", "max_reworks_per_step 不能为负")
        return merged

    def _validate_and_normalize_steps(
        self, steps: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not steps:
            raise LoopWorkflowError("empty_plan", "执行计划至少包含一个步骤")
        ids = [str(step.get("step_id", "")).strip() for step in steps]
        if any(not step_id for step_id in ids):
            raise LoopWorkflowError("missing_step_id", "每个步骤必须有 step_id")
        if len(ids) != len(set(ids)):
            raise LoopWorkflowError("duplicate_step_id", "step_id 必须唯一")
        known_ids = set(ids)
        normalized = []
        for index, raw in enumerate(steps, start=1):
            step_id = ids[index - 1]
            agent = str(raw.get("agent", "")).strip()
            if agent not in self.agent_registry:
                raise LoopWorkflowError("unknown_agent", f"步骤 {step_id} 使用未知 Agent: {agent}")
            depends_on = [str(item) for item in raw.get("depends_on", [])]
            unknown_deps = sorted(set(depends_on) - known_ids)
            if unknown_deps:
                raise LoopWorkflowError(
                    "unknown_dependency", f"步骤 {step_id} 的未知依赖: {unknown_deps}"
                )
            if step_id in depends_on:
                raise LoopWorkflowError("self_dependency", f"步骤 {step_id} 不能依赖自己")
            risk_level = str(raw.get("risk_level", "medium"))
            if risk_level not in self.policy["score_thresholds"]:
                raise LoopWorkflowError("invalid_risk", f"步骤 {step_id} 风险级别无效")
            expected_outputs = [str(item) for item in raw.get("expected_outputs", [])]
            pass_conditions = [str(item) for item in raw.get("pass_conditions", [])]
            if not expected_outputs or not pass_conditions:
                raise LoopWorkflowError(
                    "incomplete_step_contract",
                    f"步骤 {step_id} 必须定义 expected_outputs 和 pass_conditions",
                )
            normalized.append(
                {
                    "step_id": step_id,
                    "step_order": int(raw.get("step_order", index)),
                    "name": str(raw.get("name", step_id)),
                    "task_description": str(raw.get("task_description", "")),
                    "agent": agent,
                    "depends_on": depends_on,
                    "risk_level": risk_level,
                    "pass_score": float(self.policy["score_thresholds"][risk_level]),
                    "max_reworks": int(
                        raw.get("max_reworks", self.policy["max_reworks_per_step"])
                    ),
                    "expected_outputs": expected_outputs,
                    "pass_conditions": pass_conditions,
                    "status": "pending",
                    "attempt_count": 0,
                    "rework_count": 0,
                    "invalidation_count": 0,
                    "score_history": [],
                    "last_score": None,
                    "last_feedback": "",
                    "outputs": {},
                    "output_history": [],
                    "evidence_refs": [],
                    "final_pass": False,
                    "started_at": None,
                    "finished_at": None,
                }
            )
        self._assert_acyclic(normalized)
        normalized.sort(key=lambda item: (item["step_order"], item["step_id"]))
        return normalized

    def _assert_acyclic(self, steps: list[Mapping[str, Any]]) -> None:
        graph = {step["step_id"]: list(step["depends_on"]) for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise LoopWorkflowError("dependency_cycle", f"检测到依赖环: {node}")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    def _build_team_manifest(self, steps: list[Mapping[str, Any]]) -> dict[str, Any]:
        members = []
        seen = set()
        for step in steps:
            if step["agent"] not in seen:
                seen.add(step["agent"])
                members.append(step["agent"])
        return {
            "lead": "fde-lead",
            "workers": members,
            "gatekeepers": [agent for agent in members if agent in {"qa", "legal", "coach"}],
            "selection_reason": {
                agent: [step["step_id"] for step in steps if step["agent"] == agent]
                for agent in members
            },
        }

    def _refresh_ready_steps(self, run: dict[str, Any]) -> None:
        for step in run["steps"]:
            if step["status"] != "pending":
                continue
            if all(self._step(run, dep)["status"] == "passed" for dep in step["depends_on"]):
                step["status"] = "ready"
                self._event(run, "step_ready", step_id=step["step_id"])

    def _invalidate_from_roots(self, run: dict[str, Any], root_ids: list[str]) -> list[str]:
        descendants = set(root_ids)
        changed = True
        while changed:
            changed = False
            for step in run["steps"]:
                if step["step_id"] in descendants:
                    continue
                if any(dep in descendants for dep in step["depends_on"]):
                    descendants.add(step["step_id"])
                    changed = True
        for step in run["steps"]:
            if step["step_id"] not in descendants:
                continue
            if step["outputs"]:
                step["output_history"].append(copy.deepcopy(step["outputs"]))
            step["outputs"] = {}
            step["evidence_refs"] = []
            step["final_pass"] = False
            step["finished_at"] = None
            step["invalidation_count"] += 1
            step["status"] = "rework" if step["step_id"] in root_ids else "pending"
        return sorted(descendants)

    def _build_delivery_bundle(self, run: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "project_id": run["project_id"],
            "objective": run["objective"],
            "status": "awaiting_user_acceptance",
            "outputs_by_step": {
                step["step_id"]: copy.deepcopy(step["outputs"]) for step in run["steps"]
            },
            "score_summary": {
                step["step_id"]: {
                    "score": step["last_score"],
                    "threshold": step["pass_score"],
                    "rework_count": step["rework_count"],
                }
                for step in run["steps"]
            },
            "generated_at": self.clock(),
        }

    def _event(self, run: dict[str, Any], event_type: str, **data: Any) -> None:
        run["event_log"].append(
            {
                "sequence": len(run["event_log"]) + 1,
                "timestamp": self.clock(),
                "event_type": event_type,
                "data": copy.deepcopy(data),
            }
        )

    def _persist(self, run: dict[str, Any]) -> None:
        run["updated_at"] = self.clock()
        self.state_store.set(run["project_id"], self.storage_key, copy.deepcopy(run))

    def _step(self, run: Mapping[str, Any], step_id: str) -> dict[str, Any]:
        for step in run["steps"]:
            if step["step_id"] == step_id:
                return step
        raise LoopWorkflowError("step_not_found", f"找不到步骤 {step_id}")

    def _require_run_status(self, run: Mapping[str, Any], allowed: set[str]) -> None:
        if run["status"] not in allowed:
            raise LoopWorkflowError(
                "invalid_run_status",
                f"当前 run 状态 {run['status']}，允许状态 {sorted(allowed)}",
            )


__all__ = [
    "DEFAULT_AGENTS",
    "DEFAULT_POLICY",
    "LoopOrchestrator",
    "LoopWorkflowError",
    "RUN_STATES",
    "STEP_STATES",
]
