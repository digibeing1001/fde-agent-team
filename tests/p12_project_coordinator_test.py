import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.agent_team_runtime import AgentTeamRuntime
from adapters.loop_orchestrator import LoopOrchestrator
from adapters.project_coordinator import ProjectCoordinator


class MemoryStateStore:
    def __init__(self):
        self.data = {}

    def get(self, project_id, key):
        return self.data.get((project_id, key))

    def set(self, project_id, key, value):
        self.data[(project_id, key)] = value


class ProjectCoordinatorTest(unittest.TestCase):
    def test_secretary_plans_and_independent_agents_execute_with_score_gate(self):
        store = MemoryStateStore()
        ids = iter(f"id{index}" for index in range(20))
        team_runtime = AgentTeamRuntime(
            store, roles=("echo", "qa"), id_factory=lambda: next(ids)
        )
        team_runtime.provision("p1", "oc_group")
        team_runtime.activate("p1")
        loop = LoopOrchestrator(store, agent_registry=("echo", "qa"))
        worker_calls = []
        scores = iter([60, 95, 95])

        def planner(context, roles):
            self.assertEqual(["echo", "qa"], roles)
            return {
                "objective": context["business_outcome"],
                "steps": [
                    {
                        "step_id": "brief",
                        "agent": "echo",
                        "depends_on": [],
                        "expected_outputs": ["brief"],
                        "pass_conditions": ["traceable"],
                    },
                    {
                        "step_id": "verify",
                        "agent": "qa",
                        "depends_on": ["brief"],
                        "expected_outputs": ["certificate"],
                        "pass_conditions": ["reproducible"],
                    },
                ],
            }

        def worker(role, task, context):
            worker_calls.append((role, task["lead_feedback"]))
            key = "brief" if role == "echo" else "certificate"
            return {key: f"{role} result", "_evidence_refs": ["evidence://1"]}

        def scorer(step, outputs, context):
            score = next(scores)
            return {
                "rubric_scores": {
                    "goal_alignment": score,
                    "completeness": score,
                    "evidence": score,
                    "execution_quality": score,
                    "safety_compliance": score,
                    "user_constraints": score,
                },
                "feedback": "add evidence" if score < 80 else "pass",
            }

        coordinator = ProjectCoordinator(team_runtime, loop, planner, worker, scorer)
        run = coordinator.start("p1", {"business_outcome": "faster delivery"})
        self.assertEqual("awaiting_user_acceptance", run["status"])
        self.assertEqual(
            [("echo", ""), ("echo", "add evidence"), ("qa", "")], worker_calls
        )
        agents = team_runtime.get("p1")["agents"]
        echo = next(agent for agent in agents if agent["role"] == "echo")
        qa = next(agent for agent in agents if agent["role"] == "qa")
        self.assertEqual(2, len(echo["inbox"]))
        self.assertEqual(1, len(qa["inbox"]))
        self.assertNotEqual(echo["instance_id"], qa["instance_id"])


if __name__ == "__main__":
    unittest.main()
