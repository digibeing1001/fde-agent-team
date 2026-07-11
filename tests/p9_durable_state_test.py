#!/usr/bin/env python3
"""P9 regression tests for atomic state, pure validation, and replay evidence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.durable_state_store import AtomicJsonStateStore
from adapters.state_guard import StateGuard, StateMachineError


STATE_MACHINE = ROOT / "agents" / "fde-lead" / "skills" / "fde-loop-control" / "state_machine.json"


class MemoryStateStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], object] = {}

    def get(self, project_id: str, key: str):
        return self.data.get((project_id, key))

    def set(self, project_id: str, key: str, value) -> None:
        self.data[(project_id, key)] = value

    def delete(self, project_id: str, key: str) -> None:
        self.data.pop((project_id, key), None)

    def keys(self, project_id: str) -> list[str]:
        return [key for (pid, key) in self.data if pid == project_id]


class StateGuardPurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStateStore()
        self.guard = StateGuard(str(STATE_MACHINE), self.store)

    def test_failed_transition_cannot_smuggle_artifact(self) -> None:
        with self.assertRaises(StateMachineError):
            self.guard.commit_transition("p1", "done", produced_artifact="forged-final.json")
        self.assertEqual(self.guard.get_current_state("p1"), "context")
        self.assertEqual(self.store.get("p1", "produced_artifacts"), None)

        with self.assertRaises(StateMachineError):
            self.guard.commit_transition("p1", "decide", produced_artifact="wrong-plan.json")
        self.assertEqual(self.store.get("p1", "produced_artifacts"), None)

    def test_validation_does_not_persist_confirmation(self) -> None:
        self.store.set("p2", "current_state", "gate_phase")
        self.store.set("p2", "produced_artifacts", ["gate_protocol.json"])
        self.assertTrue(
            self.guard.validate_transition(
                "p2",
                "context",
                produced_artifact="user_confirmation.json",
                user_confirmed=True,
            )
        )
        self.assertEqual(self.store.get("p2", "produced_artifacts"), ["gate_protocol.json"])
        self.assertEqual(self.guard.get_current_state("p2"), "gate_phase")

    def test_budget_is_code_gated_before_state_progress_or_extra_model_call(self) -> None:
        self.store.set("p3", "runtime_usage", {"tokens": 500_001, "model_calls": 0, "tool_calls": 0})
        with self.assertRaisesRegex(StateMachineError, "budget_exhausted"):
            self.guard.commit_transition(
                "p3",
                "decide",
                produced_artifact="execution_plan.json (初版)",
            )
        self.assertEqual(self.store.get("p3", "produced_artifacts"), None)

        self.store.set("p4", "runtime_usage", {"tokens": 0, "model_calls": 64, "tool_calls": 0})
        invoked = []
        wrapped = self.guard.wrap_llm_call(lambda _messages: invoked.append(True) or "never", "p4")
        with self.assertRaisesRegex(StateMachineError, "budget_exhausted"):
            wrapped([])
        self.assertEqual(invoked, [])
        self.assertEqual(self.store.get("p4", "runtime_usage")["model_calls"], 65)


class AtomicJsonStateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fde-state-")
        self.store = AtomicJsonStateStore(self._tmp.name)
        self.guard = StateGuard(str(STATE_MACHINE), self.store)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_transition_is_atomic_durable_idempotent_and_hash_chained(self) -> None:
        result = self.guard.commit_transition(
            "project-a",
            "decide",
            produced_artifact="execution_plan.json (初版)",
            idempotency_key="host-event-1",
        )
        self.assertEqual(result, "decide")

        duplicate = self.guard.commit_transition(
            "project-a",
            "decide",
            produced_artifact="execution_plan.json (初版)",
            idempotency_key="host-event-1",
        )
        self.assertEqual(duplicate, "decide")
        self.assertEqual(self.store.verify_transition_log("project-a")["event_count"], 1)

        with self.assertRaisesRegex(StateMachineError, "idempotency_conflict"):
            self.guard.commit_transition(
                "project-a",
                "act",
                produced_artifact="validated_plan.json",
                idempotency_key="host-event-1",
            )

        with self.assertRaisesRegex(StateMachineError, "idempotency_conflict"):
            self.guard.commit_transition(
                "project-a",
                "decide",
                produced_artifact="different-artifact.json",
                idempotency_key="host-event-1",
            )

        self.guard.commit_transition(
            "project-a",
            "act",
            produced_artifact="validated_plan.json",
            idempotency_key="host-event-2",
        )
        verification = self.store.verify_transition_log("project-a")
        self.assertEqual(verification["status"], "valid")
        self.assertEqual(verification["event_count"], 2)

        reopened = AtomicJsonStateStore(self._tmp.name)
        self.assertEqual(reopened.get("project-a", "current_state"), "act")
        self.assertEqual(
            reopened.get("project-a", "produced_artifacts"),
            ["execution_plan.json (初版)", "validated_plan.json"],
        )
        events = reopened.snapshot("project-a")["transition_log"]
        self.assertEqual(events[1]["causation_id"], events[0]["event_id"])
        self.assertEqual(events[1]["previous_event_hash"], events[0]["event_hash"])

    def test_compare_and_set_rejects_stale_worker(self) -> None:
        self.store.set("project-b", "current_state", "context")
        self.assertTrue(self.store.compare_and_set("project-b", "current_state", "context", "decide"))
        self.assertFalse(self.store.compare_and_set("project-b", "current_state", "context", "act"))
        self.assertEqual(self.store.get("project-b", "current_state"), "decide")

        usage = self.guard.record_usage("project-b", tokens=100, model_calls=1, tool_calls=2)
        self.assertEqual(usage, {"tokens": 100, "model_calls": 1, "tool_calls": 2})
        usage = self.guard.record_usage("project-b", tokens=50, tool_calls=1)
        self.assertEqual(usage, {"tokens": 150, "model_calls": 1, "tool_calls": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
