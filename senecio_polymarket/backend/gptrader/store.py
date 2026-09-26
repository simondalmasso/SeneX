from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import GPTraderPaths


class GPTraderStore:
    """Local durable GPTrader state. No network persistence."""

    def __init__(self, root: str | Path | None = None):
        self.paths = GPTraderPaths.from_root(root)
        self.paths.ensure_root()
        self.paper_state_path = self.paths.root / "paper_state.json"
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
        with open(path, "ab", buffering=0) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def _atomic_json(self, path: Path, value: Any) -> None:
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self._canonical(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                value = json.loads(raw)
                if isinstance(value, dict):
                    rows.append(value)
        return rows

    def _decision_index(self) -> dict[str, dict[str, Any]]:
        if not self.paths.decisions_index.exists():
            return {}
        try:
            value = json.loads(self.paths.decisions_index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _decision_key(policy_id: str, packet_id: str) -> str:
        return f"{policy_id}|{packet_id}"

    def append_decision(self, row: dict[str, Any]) -> None:
        policy_id = str(row.get("policy_id") or "")
        packet_id = str(row.get("packet_id") or "")
        if not policy_id or not packet_id:
            raise ValueError("policy_id and packet_id are required")
        with self._lock:
            index = self._decision_index()
            key = self._decision_key(policy_id, packet_id)
            if key in index:
                raise ValueError("decision already exists")
            self._append_jsonl(self.paths.decisions, row)
            index[key] = {
                "decision_hash": row.get("decision_hash"),
                "action": row.get("action"),
                "idempotency_key": row.get("idempotency_key"),
            }
            self._atomic_json(self.paths.decisions_index, index)

    def read_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_jsonl(self.paths.decisions)

    def find_decision(self, policy_id: str, packet_id: str) -> dict[str, Any] | None:
        key = self._decision_key(policy_id, packet_id)
        with self._lock:
            meta = self._decision_index().get(key)
            if meta is None:
                return None
            for row in reversed(self._read_jsonl(self.paths.decisions)):
                if (
                    row.get("policy_id") == policy_id
                    and row.get("packet_id") == packet_id
                ):
                    return row
        return None

    def cursor_seq(self) -> int:
        if not self.paths.cursor.exists():
            return 0
        try:
            value = json.loads(self.paths.cursor.read_text(encoding="utf-8"))
            return int(value.get("packet_seq") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0

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
