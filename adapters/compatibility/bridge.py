"""Safe subprocess bridge for CLI-based agent hosts.

The bridge never uses a shell. Each independent FDE role receives a JSON work
envelope through stdin and must return one JSON result through stdout. This is
the lowest common denominator for hosts whose native team APIs differ.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


class HostBridgeError(RuntimeError):
    """Raised when a host violates the portable execution contract."""


class JsonCommandBridge:
    """Execute one isolated host process per Agent assignment."""

    def __init__(
        self,
        command_factory: Callable[[str], Sequence[str]],
        cwd: str | Path,
        timeout_seconds: int = 1800,
        runner: Optional[Callable[..., Any]] = None,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.command_factory = command_factory
        self.cwd = Path(cwd).resolve()
        self.timeout_seconds = timeout_seconds
        self.runner = runner or subprocess.run

    def execute(
        self, role: str, task: Mapping[str, Any], context: Mapping[str, Any]
    ) -> dict[str, Any]:
        command = list(self.command_factory(role))
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise HostBridgeError("command_factory returned an invalid command")
        envelope = {
            "contract_version": "fde-work-envelope-v1",
            "role": role,
            "task": copy.deepcopy(dict(task)),
            "context": copy.deepcopy(dict(context)),
            "required_response": {
                "outputs": "object",
                "evidence_refs": "array[string]",
                "constraints_followed": "array[string]",
            },
        }
        completed = self.runner(
            command,
            input=json.dumps(envelope, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=str(self.cwd),
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise HostBridgeError(
                f"{role} host process failed with {completed.returncode}: "
                f"{completed.stderr[-1000:]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise HostBridgeError(f"{role} host response is not JSON") from exc
        outputs = payload.get("outputs")
        evidence = payload.get("evidence_refs")
        constraints = payload.get("constraints_followed")
        if not isinstance(outputs, dict) or not outputs:
            raise HostBridgeError(f"{role} response must contain non-empty outputs")
        if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
            raise HostBridgeError(f"{role} response has invalid evidence_refs")
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise HostBridgeError(f"{role} response has invalid constraints_followed")
        result = copy.deepcopy(outputs)
        result["_evidence_refs"] = list(evidence)
        result["_constraints_followed"] = list(constraints)
        return result


__all__ = ["HostBridgeError", "JsonCommandBridge"]
