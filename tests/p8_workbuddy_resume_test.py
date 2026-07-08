"""
P8 test: WorkBuddy user confirmation must resume mechanically.

The regression this protects against:
    User confirms the next step, but the imported WorkBuddy agent replies with
    another plan/preparation note and stops instead of executing the next action.
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.state_guard import StateGuard
from adapters.workbuddy import WorkBuddyResumeAdapter


class MemoryStateStore:
    def __init__(self):
        self.data = {}

    def get(self, project_id, key):
        return self.data.get(f"{project_id}:{key}")

    def set(self, project_id, key, value):
        self.data[f"{project_id}:{key}"] = value

    def delete(self, project_id, key):
        self.data.pop(f"{project_id}:{key}", None)

    def keys(self, project_id):
        return [k.split(":")[1] for k in self.data if k.startswith(f"{project_id}:")]


def make_adapter(project_id="TEST-P8"):
    store = MemoryStateStore()
    sm_path = PROJECT_ROOT / "agents/fde-lead/skills/fde-loop-control/state_machine.json"
    guard = StateGuard(str(sm_path), store)
    adapter = WorkBuddyResumeAdapter(guard, store)
    return guard, store, adapter, project_id


def enter_gate_phase(guard, project_id):
    plan_artifact = guard.state_machine["states"]["context"]["transitions"][0]["artifact_required"]
    guard.commit_transition(project_id, "decide", produced_artifact=plan_artifact)
    guard.commit_transition(project_id, "act", produced_artifact="validated_plan.json")
    guard._record_artifact(project_id, "gate_protocol.json")
    guard.commit_transition(project_id, "gate_phase", produced_artifact="phase_transition.json")
    assert guard.get_current_state(project_id) == "gate_phase"


def test_confirmed_dispatch_writes_resume_payload():
    guard, store, adapter, pid = make_adapter("TEST-P8-DISPATCH")
    enter_gate_phase(guard, pid)

    signal = adapter.confirm_and_resume(
        pid,
        confirmed=True,
        actor_id="user-1",
        reason="approved next research task",
        dispatch={
            "agent": "research",
            "task": "collect market evidence",
            "constraints": ["use project knowledge base"],
        },
    )

    assert signal["kind"] == "fde-workbuddy-resume-signal"
    assert signal["control_decision"] == "continue"
    assert signal["from_state"] == "gate_phase"
    assert signal["to_state"] == "context"
    assert signal["requires_dispatch"] is True
    assert signal["next_action"]["type"] == "dispatch_worker"
    assert signal["next_action"]["tool"] == "call_research_agent"
    assert signal["mechanical_enforcement_status"] == "state_guard_committed"
    assert "Do not answer with a plan" in signal["forbidden_reply"]
    assert guard.get_current_state(pid) == "context"
    assert store.get(pid, "resume_signal") == signal
    assert store.get(pid, "last_user_confirmation")["confirmed"] is True

    payload = adapter.next_workbuddy_payload(pid)
    assert payload["kind"] == "fde-workbuddy-runtime-payload"
    assert payload["status"] == "ready"
    assert payload["must_execute"]["type"] == "dispatch_worker"
    assert payload["must_execute"]["task"] == "collect market evidence"
    assert "Do not answer with a plan" in payload["forbidden_reply"]
    assert store.get(pid, "workbuddy_next_payload") == payload
    print("[OK] confirmed dispatch writes executable WorkBuddy payload")


def test_confirmed_continue_loop_without_dispatch():
    guard, _, adapter, pid = make_adapter("TEST-P8-CONTINUE")
    enter_gate_phase(guard, pid)

    signal = adapter.confirm_and_resume(pid, confirmed=True, actor_id="user-2")

    assert signal["control_decision"] == "continue"
    assert signal["requires_dispatch"] is False
    assert signal["next_action"]["type"] == "continue_loop"
    assert signal["next_action"]["state"] == "context"
    assert guard.get_current_state(pid) == "context"
    print("[OK] confirmed resume can continue the loop without a worker dispatch")


def test_confirm_outside_gate_is_blocked():
    guard, store, adapter, pid = make_adapter("TEST-P8-BLOCK")
    assert guard.get_current_state(pid) == "context"

    signal = adapter.confirm_and_resume(pid, confirmed=True, actor_id="user-3")

    assert signal["control_decision"] == "wait_human"
    assert signal["from_state"] == "context"
    assert signal["to_state"] == "context"
    assert signal["mechanical_enforcement_status"] == "blocked_by_state_guard"
    assert store.get(pid, "resume_signal") == signal
    assert guard.get_current_state(pid) == "context"
    print("[OK] resume outside gate_phase is blocked")


def test_rejected_confirmation_aborts():
    guard, _, adapter, pid = make_adapter("TEST-P8-ABORT")
    enter_gate_phase(guard, pid)

    signal = adapter.confirm_and_resume(
        pid,
        confirmed=False,
        actor_id="user-4",
        reason="user rejected next stage",
    )

    assert signal["control_decision"] == "cancel"
    assert signal["to_state"] == "aborted"
    assert signal["next_action"]["type"] == "stop"
    assert guard.get_current_state(pid) == "aborted"

    payload = adapter.next_workbuddy_payload(pid)
    assert payload["status"] == "blocked"
    assert payload["resume_signal"] == signal
    print("[OK] rejected confirmation aborts and blocks execution payload")


def run_all_tests():
    tests = [
        test_confirmed_dispatch_writes_resume_payload,
        test_confirmed_continue_loop_without_dispatch,
        test_confirm_outside_gate_is_blocked,
        test_rejected_confirmation_aborts,
    ]

    passed = 0
    failed = 0
    failures = []
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as exc:
            failed += 1
            failures.append((test.__name__, str(exc)))
            print(f"[FAIL] {test.__name__}: {exc}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"P8 WorkBuddy resume tests: {passed}/{passed + failed} passed")
    if failures:
        for name, error in failures:
            print(f"  - {name}: {error}")
    print(f"{'=' * 60}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all_tests() else 1)
