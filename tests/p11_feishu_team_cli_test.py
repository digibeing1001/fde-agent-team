import unittest

from adapters.agent_team_runtime import AgentTeamRuntime
from adapters.feishu.team_cli import FeishuTeamProvisioner, LarkCLI, SocraticIntake


class MemoryStateStore:
    def __init__(self):
        self.data = {}

    def get(self, project_id, key):
        return self.data.get((project_id, key))

    def set(self, project_id, key, value):
        self.data[(project_id, key)] = value


class FakeExecutor:
    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(list(command))
        if command[1:3] == ["auth", "status"]:
            return {"ok": True, "identity": "bot"}
        if "+chat-create" in command:
            return {"ok": True, "data": {"chat_id": "oc_project"}}
        if "chat.members" in command:
            return {"ok": True, "data": {"invalid_id_list": []}}
        return {"ok": True, "data": {"message_id": "om_1"}}


class FeishuTeamCLITest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStateStore()
        self.executor = FakeExecutor()
        self.ids = iter(f"id{index}" for index in range(50))
        runtime = AgentTeamRuntime(
            self.store,
            roles=("echo", "qa", "legal"),
            id_factory=lambda: next(self.ids),
            clock=lambda: "2026-07-23T00:00:00Z",
        )
        self.provisioner = FeishuTeamProvisioner(
            LarkCLI(executor=self.executor), self.store, runtime
        )

    def test_import_has_one_visible_bot_and_independent_worker_roles(self):
        manifest = self.provisioner.import_team(
            {
                "visible_bot_role": "fde-lead",
                "roles": ["echo", "qa", "legal"],
            }
        )
        self.assertEqual(1, manifest["visible_bot_count"])
        self.assertEqual(["echo", "qa", "legal"], manifest["independent_agent_roles"])
        self.assertIn("--verify", self.executor.commands[0])

    def test_project_start_creates_group_members_and_real_agent_instances(self):
        self.provisioner.import_team({"roles": ["echo", "qa", "legal"]})
        result = self.provisioner.start_project(
            "p1", "客户增长诊断", "ou_owner", ["ou_owner", "ou_sponsor"]
        )
        team = result["team"]
        self.assertEqual("oc_project", result["project"]["chat_id"])
        self.assertEqual(3, len(team["agents"]))
        self.assertEqual(3, len({item["instance_id"] for item in team["agents"]}))
        self.assertTrue(all(item["inbox"] == [] for item in team["agents"]))
        self.assertEqual(1, team["transport"]["visible_feishu_bots"])
        flattened = [part for command in self.executor.commands for part in command]
        self.assertIn("+chat-create", flattened)
        self.assertIn("chat.members", flattened)
        self.assertIn("+messages-send", flattened)

    def test_socratic_intake_asks_exactly_one_question_each_turn(self):
        intake = SocraticIntake(self.store)
        turn = intake.begin("p1")
        self.assertEqual({"field", "text"}, set(turn["question"]))
        first_field = turn["question"]["field"]
        same_turn = intake.next_turn("p1")
        self.assertEqual(turn, same_turn)
        next_turn = intake.answer("p1", first_field, "可量化地降低交付周期")
        self.assertNotEqual(first_field, next_turn["question"]["field"])
        self.assertNotIn("questions", next_turn)

    def test_complete_context_auto_activates_all_agents(self):
        self.provisioner.import_team({"roles": ["echo", "qa", "legal"]})
        context = {
            "project_type": "fde_ai_consulting",
            "business_outcome": "reduce onboarding time",
            "users_and_workflow": "implementation consultants onboard clients",
            "success_measure": "30 percent faster",
            "constraints": "no production data export",
            "available_evidence": "sandbox and interviews",
        }
        result = self.provisioner.start_project(
            "p1", "Onboarding", "ou_owner", initial_context=context
        )
        self.assertEqual("active", result["project"]["status"])
        persisted = self.provisioner.team_runtime.get("p1")
        self.assertTrue(all(agent["lifecycle"] == "active" for agent in persisted["agents"]))

    def test_non_fde_project_is_rejected(self):
        intake = SocraticIntake(self.store)
        with self.assertRaisesRegex(ValueError, "仅支持 FDE"):
            intake.begin("p1", {"project_type": "generic_marketing"})

    def test_worker_message_keeps_instance_identity_over_shared_bot(self):
        self.provisioner.import_team({"roles": ["echo", "qa", "legal"]})
        context = {
            key: "known"
            for key in (
                "business_outcome",
                "users_and_workflow",
                "success_measure",
                "constraints",
                "available_evidence",
            )
        }
        self.provisioner.start_project("p1", "Project", "ou_owner", initial_context=context)
        envelope = self.provisioner.relay_agent_message("p1", "echo", {"text": "需求基线完成"})
        self.assertEqual("echo", envelope["role"])
        self.assertTrue(envelope["agent_instance_id"].startswith("echo-"))


if __name__ == "__main__":
    unittest.main()
