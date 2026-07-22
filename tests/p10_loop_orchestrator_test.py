import unittest

from adapters.loop_orchestrator import LoopOrchestrator, LoopWorkflowError


class MemoryStateStore:
    def __init__(self):
        self.data = {}

    def get(self, project_id, key):
        return self.data.get((project_id, key))

    def set(self, project_id, key, value):
        self.data[(project_id, key)] = value


RUBRIC_PASS = {
    "goal_alignment": 90,
    "completeness": 90,
    "evidence": 90,
    "execution_quality": 90,
    "safety_compliance": 90,
    "user_constraints": 90,
}
RUBRIC_FAIL = {key: 50 for key in RUBRIC_PASS}


def plan():
    return [
        {
            "step_id": "discover",
            "name": "需求发现",
            "agent": "echo",
            "risk_level": "medium",
            "depends_on": [],
            "expected_outputs": ["brief"],
            "pass_conditions": ["success metric is explicit"],
        },
        {
            "step_id": "validate",
            "name": "风险验证",
            "agent": "qa",
            "risk_level": "high",
            "depends_on": ["discover"],
            "expected_outputs": ["report"],
            "pass_conditions": ["evidence is reproducible"],
        },
    ]


class LoopOrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStateStore()
        self.loop = LoopOrchestrator(
            self.store,
            clock=lambda: "2026-07-23T00:00:00Z",
        )

    def _submit_and_score(self, step_id, output_name, scores):
        self.loop.start_step("p1", step_id)
        self.loop.submit_step_result(
            "p1", step_id, {output_name: "artifact"}, ["evidence://1"]
        )
        return self.loop.score_step("p1", step_id, scores, "Lead review")

    def test_dynamic_team_and_dependency_gate(self):
        run = self.loop.create_run("p1", "deploy an FDE solution", plan())
        self.assertEqual(["echo", "qa"], run["team_manifest"]["workers"])
        self.assertEqual("ready", run["steps"][0]["status"])
        self.assertEqual("pending", run["steps"][1]["status"])
        with self.assertRaises(LoopWorkflowError):
            self.loop.start_step("p1", "validate")

    def test_every_step_is_scored_and_failed_work_returns_to_same_node(self):
        self.loop.create_run("p1", "deploy an FDE solution", plan())
        result = self._submit_and_score("discover", "brief", RUBRIC_FAIL)
        self.assertFalse(result["passed"])
        self.assertEqual("rework", result["step"]["status"])
        self.assertEqual("echo", result["step"]["agent"])
        self.assertEqual(1, result["step"]["rework_count"])
        result = self._submit_and_score("discover", "brief", RUBRIC_PASS)
        self.assertTrue(result["passed"])
        self.assertEqual("ready", self.loop.get_run("p1")["steps"][1]["status"])

    def test_user_rejection_invalidates_root_and_downstream_then_acceptance_reviews_failures(self):
        self.loop.create_run("p1", "deploy an FDE solution", plan())
        self._submit_and_score("discover", "brief", RUBRIC_FAIL)
        self._submit_and_score("discover", "brief", RUBRIC_PASS)
        self._submit_and_score("validate", "report", RUBRIC_PASS)
        run = self.loop.get_run("p1")
        self.assertEqual("awaiting_user_acceptance", run["status"])

        run = self.loop.record_user_feedback(
            "p1", False, "requirements are wrong", ["discover"]
        )
        self.assertEqual("rework", run["steps"][0]["status"])
        self.assertEqual("pending", run["steps"][1]["status"])
        self.assertEqual(1, run["steps"][1]["invalidation_count"])

        self._submit_and_score("discover", "brief", RUBRIC_PASS)
        self._submit_and_score("validate", "report", RUBRIC_PASS)
        run = self.loop.record_user_feedback("p1", True, "accepted")
        self.assertEqual("accepted", run["status"])
        self.assertEqual("discover", run["retrospective_queue"][0]["step_id"])

    def test_rejection_requires_lead_diagnosis(self):
        self.loop.create_run("p1", "deploy an FDE solution", plan()[:1])
        self._submit_and_score("discover", "brief", RUBRIC_PASS)
        with self.assertRaisesRegex(LoopWorkflowError, "root_cause_step_ids"):
            self.loop.record_user_feedback("p1", False, "not accepted")

    def test_audit_tracks_scores_rework_and_final_pass(self):
        self.loop.create_run("p1", "deploy an FDE solution", plan()[:1])
        self._submit_and_score("discover", "brief", RUBRIC_FAIL)
        self._submit_and_score("discover", "brief", RUBRIC_PASS)
        audit = self.loop.audit_report("p1")
        step = audit["steps"][0]
        self.assertEqual([50.0, 90.0], [item["weighted_score"] for item in step["score_history"]])
        self.assertEqual(2, step["attempt_count"])
        self.assertEqual(1, step["rework_count"])
        self.assertTrue(step["final_pass"])


if __name__ == "__main__":
    unittest.main()
