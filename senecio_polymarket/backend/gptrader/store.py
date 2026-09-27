from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import GPTraderPaths


class DecisionLogError(ValueError):
    pass


class DecisionLogCorruptionError(DecisionLogError):
    def __init__(self, message: str, *, line_no: int, raw: bytes):
        super().__init__(message)
        self.line_no = int(line_no)
        self.raw_sha256 = hashlib.sha256(raw).hexdigest()
        self.raw_bytes = len(raw)


class DecisionLogConflictError(DecisionLogError):
    def __init__(self, key: str):
        super().__init__(f"conflicting duplicate decision in durable log: {key}")
        self.key = key


class CursorStateError(RuntimeError):
    pass


class RootOwnershipError(RuntimeError):
    pass


def _decision_key(policy_id: str, packet_id: str) -> str:
    return f"{policy_id}|{packet_id}"


def _decision_meta(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_hash": row.get("decision_hash"),
        "action": row.get("action"),
        "idempotency_key": row.get("idempotency_key"),
    }


def logical_decision_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        policy_id = str(row.get("policy_id") or "")
        packet_id = str(row.get("packet_id") or "")
        if not policy_id or not packet_id:
            out.append(row)
            continue
        key = _decision_key(policy_id, packet_id)
        meta = _decision_meta(row)
        prior = first.get(key)
        if prior is None:
            first[key] = meta
            out.append(row)
            continue
        if prior == meta:
            continue
        raise DecisionLogConflictError(key)
    return out


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class GPTraderRootLease:
    def __init__(self, root: Path):
        self.path = Path(root) / ".gptrader.owner.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        try:
            self._acquire()
        except Exception:
            self._handle.close()
            raise

    def _acquire(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0, os.SEEK_END)
                if self._handle.tell() == 0:
                    self._handle.write(b"0")
                    self._handle.flush()
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise RootOwnershipError("GPTRADER_STATE_ROOT_ALREADY_OWNED") from exc

    def close(self) -> None:
        if self._handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class GPTraderStore:
    """Local durable GPTrader state. No network persistence."""

    def __init__(self, root: str | Path | None = None):
        self.paths = GPTraderPaths.from_root(root)
        self.paths.ensure_root()
        self.paper_state_path = self.paths.root / "paper_state.json"
        self.decision_quarantine_path = self.paths.root / "decision_log.quarantine.json"
        self._lock = threading.RLock()

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        encoded = (self._canonical(row) + "\n").encode("utf-8")
        created = not path.exists()
        with open(path, "ab", buffering=0) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            _fsync_parent(path)

    def _atomic_json(self, path: Path, value: Any) -> None:
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self._canonical(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_parent(path)

    @staticmethod
    def _read_jsonl(
        path: Path,
        *,
        recover_torn_tail: bool = False,
    ) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        raw_lines = path.read_bytes().splitlines(keepends=True)
        valid_bytes = 0
        for index, raw in enumerate(raw_lines):
            stripped = raw.strip()
            if not stripped:
                valid_bytes += len(raw)
                continue
            try:
                value = json.loads(stripped)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                is_last = index == len(raw_lines) - 1
                terminated = raw.endswith((b"\n", b"\r"))
                if recover_torn_tail and is_last and not terminated:
                    with open(path, "r+b") as handle:
                        handle.truncate(valid_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                    break
                raise DecisionLogCorruptionError(
                    f"corrupt decision log at line {index + 1}",
                    line_no=index + 1,
                    raw=raw,
                ) from exc
            if isinstance(value, dict):
                rows.append(value)
            valid_bytes += len(raw)
        return rows

    def _mark_decision_quarantine(self, payload: dict[str, Any]) -> None:
        marker = {
            "status": "QUARANTINED",
            **payload,
        }
        self._atomic_json(self.decision_quarantine_path, marker)

    def _read_decision_rows(self) -> list[dict[str, Any]]:
        try:
            return self._read_jsonl(self.paths.decisions, recover_torn_tail=True)
        except DecisionLogCorruptionError as exc:
            self._mark_decision_quarantine(
                {
                    "reason": "CORRUPT_DECISION_LOG",
                    "line_no": exc.line_no,
                    "raw_sha256": exc.raw_sha256,
                    "raw_bytes": exc.raw_bytes,
                }
            )
            raise

    def _decision_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        if self.paths.decisions_index.exists():
            try:
                value = json.loads(self.paths.decisions_index.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    index = value
            except (OSError, json.JSONDecodeError):
                index = {}

        try:
            logical = logical_decision_rows(self._read_decision_rows())
        except DecisionLogConflictError as exc:
            self._mark_decision_quarantine(
                {
                    "reason": "CONFLICTING_DUPLICATE_DECISION",
                    "key": exc.key,
                }
            )
            raise

        authoritative: dict[str, dict[str, Any]] = {}
        for row in logical:
            policy_id = str(row.get("policy_id") or "")
            packet_id = str(row.get("packet_id") or "")
            if not policy_id or not packet_id:
                continue
            authoritative[_decision_key(policy_id, packet_id)] = _decision_meta(row)

        if authoritative != index:
            self._atomic_json(self.paths.decisions_index, authoritative)
        return authoritative

    def append_decision(self, row: dict[str, Any]) -> None:
        policy_id = str(row.get("policy_id") or "")
        packet_id = str(row.get("packet_id") or "")
        if not policy_id or not packet_id:
            raise ValueError("policy_id and packet_id are required")
        with self._lock:
            index = self._decision_index()
            key = _decision_key(policy_id, packet_id)
            if key in index:
                raise ValueError("decision already exists")
            self._append_jsonl(self.paths.decisions, row)
            index[key] = _decision_meta(row)
            self._atomic_json(self.paths.decisions_index, index)

    def read_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                return logical_decision_rows(self._read_decision_rows())
            except DecisionLogConflictError as exc:
                self._mark_decision_quarantine(
                    {
                        "reason": "CONFLICTING_DUPLICATE_DECISION",
                        "key": exc.key,
                    }
                )
                raise

    def find_decision(self, policy_id: str, packet_id: str) -> dict[str, Any] | None:
        key = _decision_key(policy_id, packet_id)
        with self._lock:
            meta = self._decision_index().get(key)
            if meta is None:
                return None
            for row in self.read_decisions():
                if (
                    row.get("policy_id") == policy_id
                    and row.get("packet_id") == packet_id
                ):
                    return row
        return None

    def cursor_state(self) -> dict[str, Any]:
        if not self.paths.cursor.exists():
            return {"status": "MISSING", "packet_seq": 0}
        try:
            value = json.loads(self.paths.cursor.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("cursor must be an object")
            packet_seq = value.get("packet_seq")
            if isinstance(packet_seq, bool) or not isinstance(packet_seq, int) or packet_seq < 0:
                raise ValueError("cursor packet_seq is invalid")
            cursor = value.get("cursor")
            if not isinstance(cursor, str) or not cursor:
                raise ValueError("cursor token is invalid")
            return {"status": "OK", "packet_seq": packet_seq, "cursor": cursor}
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {"status": "CORRUPT", "packet_seq": None, "error": type(exc).__name__}

    def cursor_seq(self) -> int:
        state = self.cursor_state()
        if state["status"] == "CORRUPT":
            raise CursorStateError("GPTRADER_CURSOR_CORRUPT")
        return int(state["packet_seq"])

    def decision_log_health(self) -> dict[str, Any]:
        if self.decision_quarantine_path.exists():
            try:
                marker = json.loads(self.decision_quarantine_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                marker = {"status": "QUARANTINED", "reason": "UNREADABLE_QUARANTINE_MARKER"}
            return {"ok": False, **marker}
        try:
            self._decision_index()
        except DecisionLogError as exc:
            if self.decision_quarantine_path.exists():
                try:
                    marker = json.loads(
                        self.decision_quarantine_path.read_text(encoding="utf-8")
                    )
                    if isinstance(marker, dict):
                        return {"ok": False, **marker}
                except (OSError, json.JSONDecodeError):
                    pass
            return {"ok": False, "status": "QUARANTINED", "reason": type(exc).__name__}
        return {"ok": True, "status": "OK"}

    def acquire_runtime_lease(self) -> GPTraderRootLease:
        return GPTraderRootLease(self.paths.root)

    def set_cursor_seq(self, packet_seq: int, cursor: str) -> None:
        if isinstance(packet_seq, bool) or int(packet_seq) < 0:
            raise ValueError("packet_seq must be non-negative")
        with self._lock:
            current = self.cursor_seq()
            if int(packet_seq) < current:
                raise ValueError("cursor rewind forbidden")
            self._atomic_json(
                self.paths.cursor,
                {"packet_seq": int(packet_seq), "cursor": str(cursor)},
            )

    def append_run(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._append_jsonl(self.paths.runs, row)

    def read_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_jsonl(self.paths.runs)

    def append_mcp_audit(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._append_jsonl(self.paths.mcp_audit, row)

    def find_packet(self, packet_id: str) -> dict[str, Any] | None:
        with self._lock:
            for row in self._read_jsonl(self.paths.sealed_packets):
                if row.get("packet_id") == packet_id:
                    return row
        return None

    def read_packets(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_jsonl(self.paths.sealed_packets)

    def save_paper_state(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._atomic_json(self.paper_state_path, value)

    def load_paper_state(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.paper_state_path.exists():
                return None
            value = json.loads(self.paper_state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None

    def read_trades(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self._read_jsonl(self.paths.trades)
        if limit is None:
            return rows
        return rows[-max(0, int(limit)) :]
