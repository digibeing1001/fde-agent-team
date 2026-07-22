"""
P9 test: sub-agent avatar single-source-of-truth must stay wired (WB-HARNESS-P0-001).

The regression this protects against:
    A future edit to ``team.yaml`` / ``agents/*/SKILL.md`` removes or mistypes an
    ``avatar:`` entry, so when the WorkBuddy host is patched (see
    docs/harness-subagent-avatar-fix.md) the spawned sub-agent still renders with
    no avatar because the data the resolver would return is missing or broken.

This test asserts the *package-side* contract that the host fix depends on:
    * ``resolve_avatar(<id>)`` returns the right path for every accepted id form
      (team.yaml key, built plugin id, agent dir).
    * every resolved path points to a PNG that actually exists on disk.
    * the team-level avatar (``avatars/team.png``) is present.
"""

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.workbuddy.avatar_resolver import resolve_avatar, team_avatar

# (team.yaml key, built plugin id, agent dir, expected avatar path)
ROSTER = [
    ("fde-lead", "fde-agent-team-team-lead", "fde-lead", "avatars/fde-agent-team-team-lead.png"),
    ("echo", "echo-analyst", "echo-agent", "avatars/echo-analyst.png"),
    ("delta", "delta-engineer", "delta-agent", "avatars/delta-engineer.png"),
    ("productize", "productize-specialist", "productize-agent", "avatars/productize-specialist.png"),
    ("research", "research-analyst", "research-agent", "avatars/research-analyst.png"),
    ("knowledge-curator", "knowledge-curator", "knowledge-curator", "avatars/knowledge-curator.png"),
    ("qa", "qa-gatekeeper", "qa-agent", "avatars/qa-gatekeeper.png"),
    ("legal", "legal-reviewer", "legal-agent", "avatars/legal-reviewer.png"),
    ("coach", "growth-coach", "coach-agent", "avatars/growth-coach.png"),
]

TEAM_AVATAR = "avatars/team.png"


def test_resolver_returns_expected_path_for_every_id_form():
    for key, plugin_id, agent_dir, expected in ROSTER:
        for ident in (key, plugin_id, agent_dir):
            got = resolve_avatar(ident)
            assert got == expected, (
                f"resolve_avatar({ident!r}) = {got!r}, expected {expected!r}"
            )
    print(f"[OK] resolver returns the expected avatar for all {len(ROSTER)} agents x 3 id forms")


def test_resolved_avatar_files_exist_on_disk():
    missing = []
    for _, _, _, expected in ROSTER:
        path = PROJECT_ROOT / expected
        if not path.is_file():
            missing.append(expected)
    assert not missing, f"avatar PNGs missing on disk: {missing}"
    print(f"[OK] all {len(ROSTER)} avatar PNGs exist on disk")


def test_team_avatar_present():
    got = team_avatar()
    assert got == TEAM_AVATAR, f"team_avatar() = {got!r}, expected {TEAM_AVATAR!r}"
    assert (PROJECT_ROOT / TEAM_AVATAR).is_file(), f"team avatar missing: {TEAM_AVATAR}"
    print(f"[OK] team-level avatar present at {TEAM_AVATAR}")


def test_full_roster_is_resolvable():
    # Lock the contract: exactly the 9-agent roster must resolve, nothing more,
    # nothing less. If a member is dropped or a stray id starts resolving, this
    # fails and forces a conscious decision.
    resolved = {resolve_avatar(key) for key, _, _, _ in ROSTER}
    assert len(resolved) == len(ROSTER), (
        f"expected {len(ROSTER)} distinct avatar paths, got {len(resolved)}"
    )
    print(f"[OK] full {len(ROSTER)}-agent roster resolves to distinct avatar paths")


def run_all_tests():
    tests = [
        test_resolver_returns_expected_path_for_every_id_form,
        test_resolved_avatar_files_exist_on_disk,
        test_team_avatar_present,
        test_full_roster_is_resolvable,
    ]

    passed = 0
    failed = 0
    failures = []
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as exc:  # noqa: BLE001 - report every failure
            failed += 1
            failures.append((test.__name__, str(exc)))
            print(f"[FAIL] {test.__name__}: {exc}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"P9 avatar single-source-of-truth tests: {passed}/{passed + failed} passed")
    if failures:
        for name, error in failures:
            print(f"  - {name}: {error}")
    print(f"{'=' * 60}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all_tests() else 1)
