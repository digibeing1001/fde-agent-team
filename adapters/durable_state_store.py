"""Local durable state and transition ledger for FDE Agent Team.

The store deliberately uses only the Python standard library so it can run in
Hermes/OpenClaw/WorkBuddy hosts without installing a database driver. It is a
single-host reference implementation; distributed deployments should provide a
StateStore with equivalent compare-and-set and transactional transition methods.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from adapters.base import StateStore


try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - exercised on Windows
    _HAS_FCNTL = False


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class _CrossProcessFileLock:
    """Small cross-platform advisory lock used around one project snapshot."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: Optional[int] = None

    def __enter__(self) -> "_CrossProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        if _HAS_FCNTL:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        else:  # Windows
            import msvcrt

            if os.fstat(self._fd).st_size == 0:
                os.write(self._fd, b"\0")
                os.fsync(self._fd)
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._fd is None:
            return
        if _HAS_FCNTL:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        else:  # Windows
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        os.close(self._fd)
        self._fd = None


class AtomicJsonStateStore(StateStore):
    """Atomic single-host StateStore with CAS and a hash-chained event ledger."""

    schema_version = "1.0.0"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.projects_dir = self.root / "projects"
        self.locks_dir = self.root / "locks"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _project_token(project_id: str) -> str:
        project_id = str(project_id).strip()
        if not project_id or len(project_id) > 512 or any(ord(char) < 32 for char in project_id):
            raise ValueError("project_id must be a non-empty printable string of at most 512 characters")
        return hashlib.sha256(project_id.encode("utf-8")).hexdigest()

    def _path(self, project_id: str) -> Path:
        return self.projects_dir / f"{self._project_token(project_id)}.json"

    def _lock(self, project_id: str) -> _CrossProcessFileLock:
        return _CrossProcessFileLock(self.locks_dir / f"{self._project_token(project_id)}.lock")

    def _empty(self, project_id: str) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "fde-project-state",
            "project_id": project_id,
            "updated_at": _now_iso(),
            "values": {},
            "transition_log": [],
            "idempotency_index": {},
        }

    def _read_unlocked(self, project_id: str) -> dict[str, Any]:
        path = self._path(project_id)
        if not path.exists():
            return self._empty(project_id)
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("kind") != "fde-project-state" or data.get("project_id") != project_id:
            raise ValueError(f"invalid or mismatched FDE state snapshot: {path}")
        if not isinstance(data.get("values"), dict) or not isinstance(data.get("transition_log"), list):
            raise ValueError(f"invalid FDE state snapshot structure: {path}")
        data.setdefault("idempotency_index", {})
        return data

    def _write_unlocked(self, project_id: str, data: dict[str, Any]) -> None:
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = _now_iso()
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    def get(self, project_id: str, key: str) -> Any:
        with self._lock(project_id):
            return self._read_unlocked(project_id)["values"].get(key)

    def set(self, project_id: str, key: str, value: Any) -> None:
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            data["values"][key] = value
            self._write_unlocked(project_id, data)

    def delete(self, project_id: str, key: str) -> None:
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            data["values"].pop(key, None)
            self._write_unlocked(project_id, data)

    def keys(self, project_id: str) -> list[str]:
        with self._lock(project_id):
            return sorted(self._read_unlocked(project_id)["values"].keys())

    def compare_and_set(self, project_id: str, key: str, expected: Any, value: Any) -> bool:
        """Atomically set ``key`` only when its current value equals ``expected``."""
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            if data["values"].get(key) != expected:
                return False
            data["values"][key] = value
            self._write_unlocked(project_id, data)
            return True

    def increment_counters(self, project_id: str, key: str, additions: dict[str, int]) -> dict[str, int]:
        """Atomically add non-negative values to a counter dictionary."""
        normalized = {str(name): int(value) for name, value in additions.items()}
        if any(value < 0 for value in normalized.values()):
            raise ValueError("counter additions must be non-negative")
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            counters = dict(data["values"].get(key) or {})
            for name, value in normalized.items():
                counters[name] = int(counters.get(name, 0) or 0) + value
            data["values"][key] = counters
            self._write_unlocked(project_id, data)
            return counters

    def transition_count(self, project_id: str) -> int:
        with self._lock(project_id):
            return len(self._read_unlocked(project_id)["transition_log"])

    def get_transition_by_idempotency(self, project_id: str, idempotency_key: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            event_id = data.get("idempotency_index", {}).get(idempotency_key)
            if not event_id:
                return None
            return next((event for event in data["transition_log"] if event.get("event_id") == event_id), None)

    def commit_state_transition(
        self,
        project_id: str,
        *,
        expected_state: str,
        target_state: str,
        artifacts: list[str] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Atomically commit state, artifacts, idempotency index, and ledger event."""
        artifacts = list(dict.fromkeys(str(item) for item in (artifacts or []) if str(item)))
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
            if idempotency_key:
                previous_id = data["idempotency_index"].get(idempotency_key)
                if previous_id:
                    previous = next(
                        (event for event in data["transition_log"] if event.get("event_id") == previous_id),
                        None,
                    )
                    if (
                        previous
                        and previous.get("to_state") == target_state
                        and list(previous.get("artifacts") or []) == artifacts
                    ):
                        return {"status": "idempotent", "event": previous, "current_state": data["values"].get("current_state")}
                    return {
                        "status": "idempotency_conflict",
                        "event": previous,
                        "current_state": data["values"].get("current_state"),
                    }
            current = data["values"].get("current_state")
            if current != expected_state:
                return {"status": "conflict", "current_state": current, "expected_state": expected_state}

            produced = list(data["values"].get("produced_artifacts") or [])
            for artifact in artifacts:
                if artifact not in produced:
                    produced.append(artifact)
            data["values"]["current_state"] = target_state
            data["values"]["produced_artifacts"] = produced

            previous = data["transition_log"][-1] if data["transition_log"] else None
            event = {
                "schema_version": "1.0.0",
                "kind": "fde-state-transition-event",
                "event_id": uuid.uuid4().hex,
                "sequence": len(data["transition_log"]) + 1,
                "timestamp": _now_iso(),
                "project_id": project_id,
                "correlation_id": project_id,
                "causation_id": str((previous or {}).get("event_id", "")),
                "idempotency_key": idempotency_key,
                "from_state": expected_state,
                "to_state": target_state,
                "artifacts": artifacts,
                "previous_event_hash": str((previous or {}).get("event_hash", "")),
            }
            event["event_hash"] = _canonical_hash(event)
            data["transition_log"].append(event)
            if idempotency_key:
                data["idempotency_index"][idempotency_key] = event["event_id"]
            self._write_unlocked(project_id, data)
            return {"status": "committed", "event": event, "current_state": target_state}

    def snapshot(self, project_id: str) -> dict[str, Any]:
        with self._lock(project_id):
            return self._read_unlocked(project_id)

    def verify_transition_log(self, project_id: str) -> dict[str, Any]:
        """Verify sequence, causation, idempotency, and the hash chain."""
        with self._lock(project_id):
            data = self._read_unlocked(project_id)
        issues: list[dict[str, Any]] = []
        previous_hash = ""
        previous_id = ""
        seen_ids: set[str] = set()
        seen_keys: set[str] = set()
        for index, event in enumerate(data["transition_log"], start=1):
            event_id = str(event.get("event_id", ""))
            if not event_id or event_id in seen_ids:
                issues.append({"sequence": index, "issue": "missing_or_duplicate_event_id"})
            seen_ids.add(event_id)
            if event.get("sequence") != index:
                issues.append({"sequence": index, "issue": "sequence_mismatch"})
            if str(event.get("previous_event_hash", "")) != previous_hash:
                issues.append({"sequence": index, "issue": "previous_event_hash_mismatch"})
            if str(event.get("causation_id", "")) != previous_id:
                issues.append({"sequence": index, "issue": "causation_id_mismatch"})
            expected_hash = _canonical_hash({key: value for key, value in event.items() if key != "event_hash"})
            if not hmac.compare_digest(expected_hash, str(event.get("event_hash", ""))):
                issues.append({"sequence": index, "issue": "event_hash_mismatch"})
            idempotency_key = str(event.get("idempotency_key", ""))
            if idempotency_key and idempotency_key in seen_keys:
                issues.append({"sequence": index, "issue": "duplicate_idempotency_key"})
            if idempotency_key:
                seen_keys.add(idempotency_key)
            previous_hash = str(event.get("event_hash", ""))
            previous_id = event_id
        return {
            "status": "valid" if not issues else "invalid",
            "project_id": project_id,
            "event_count": len(data["transition_log"]),
            "last_event_hash": previous_hash,
            "issues": issues,
        }
