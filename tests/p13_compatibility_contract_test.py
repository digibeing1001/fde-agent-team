import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.compatibility.bridge import HostBridgeError, JsonCommandBridge
from adapters.compatibility.compiler import (
    CompatibilityCompiler,
    CompatibilityInstallError,
    ROLE_CARDS,
)
from adapters.compatibility.registry import CompatibilityRegistry, REQUIRED_INVARIANTS


ROOT = Path(__file__).resolve().parents[1]


class CompatibilityContractTest(unittest.TestCase):
    def test_researched_platforms_cover_the_same_portable_contract(self):
        registry = CompatibilityRegistry.from_file(ROOT / "config" / "host-capabilities.json")
        platforms = registry.list_platforms()
        self.assertGreaterEqual(len(platforms), 14)
        for platform in platforms:
            with self.subTest(platform=platform["id"]):
                result = registry.assess(platform["id"])
                self.assertTrue(result["contract_compatible"], result)
                self.assertEqual([], result["missing_invariants"])
                self.assertTrue(platform["sources"])

    def test_p0_hosts_are_explicit_and_not_all_claimed_native(self):
        registry = CompatibilityRegistry.from_file(ROOT / "config" / "host-capabilities.json")
        p0 = {item["id"] for item in registry.list_platforms("P0")}
        self.assertTrue(
            {
                "claude_code",
                "codex",
                "gemini_cli",
                "github_copilot",
                "opencode",
                "openclaw",
                "hermes_agent",
                "workbuddy",
            }.issubset(p0)
        )
        self.assertFalse(registry.assess("codex")["native_parity"])
        self.assertFalse(registry.assess("workbuddy")["native_parity"])

    def _synthetic_repository(self, root):
        for path in ROLE_CARDS.values():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "---\nname: fixture\ndescription: fixture\n---\n\n# Role\n\nDo scoped work.\n",
                encoding="utf-8",
            )

    def test_profile_compiler_emits_all_independent_roles_and_one_contract(self):
        registry = CompatibilityRegistry.from_file(ROOT / "config" / "host-capabilities.json")
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp) / "repo"
            repository.mkdir()
            self._synthetic_repository(repository)
            compiler = CompatibilityCompiler(repository, registry)
            for host in ("claude_code", "gemini_cli", "github_copilot", "opencode", "codex"):
                with self.subTest(host=host):
                    files = compiler.render(host)
                    manifest = json.loads(files[".fde/host-manifest.json"])
                    self.assertEqual(list(REQUIRED_INVARIANTS), manifest["required_invariants"])
                    self.assertEqual(list(ROLE_CARDS), manifest["roles"])
                    self.assertEqual(len(ROLE_CARDS) + 2, len(files))
                    worker_text = "\n".join(files.values())
                    self.assertIn("独立 Agent 实例", worker_text)
                    self.assertIn("portable kernel", files["FDE-TEAM.md"])

    def test_installer_refuses_silent_overwrite(self):
        registry = CompatibilityRegistry.from_file(ROOT / "config" / "host-capabilities.json")
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp) / "repo"
            target = Path(temp) / "target"
            repository.mkdir()
            target.mkdir()
            self._synthetic_repository(repository)
            compiler = CompatibilityCompiler(repository, registry)
            installed = compiler.install("claude_code", target)
            self.assertIn("FDE-TEAM.md", installed)
            with self.assertRaises(CompatibilityInstallError):
                compiler.install("claude_code", target)

    def test_json_command_bridge_enforces_work_envelope_without_shell(self):
        response = {
            "outputs": {"brief": "done"},
            "evidence_refs": ["evidence://1"],
            "constraints_followed": ["language=zh"],
        }
        code = "import json,sys; json.load(sys.stdin); print(" + repr(json.dumps(response)) + ")"
        bridge = JsonCommandBridge(
            lambda role: [sys.executable, "-c", code], ROOT, timeout_seconds=10
        )
        result = bridge.execute("echo", {"step_id": "s1"}, {"language": "zh"})
        self.assertEqual("done", result["brief"])
        self.assertEqual(["evidence://1"], result["_evidence_refs"])

    def test_json_command_bridge_rejects_unscored_plain_text(self):
        class Result:
            returncode = 0
            stdout = "not-json"
            stderr = ""

        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        bridge = JsonCommandBridge(lambda role: ["fake-host", role], ROOT, runner=runner)
        with self.assertRaises(HostBridgeError):
            bridge.execute("qa", {"step_id": "s1"}, {})
        self.assertFalse(calls[0][1]["shell"])


if __name__ == "__main__":
    unittest.main()
