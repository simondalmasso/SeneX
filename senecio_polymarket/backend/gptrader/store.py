from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import GPTraderPaths


class GPTraderStore:
    """Append-only/local durable GPTrader state with no network persistence."""

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
        tmp = path.with_name(f".{path.name}.tmp")
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

    def append_decision(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._append_jsonl(self.paths.decisions, row)

    def read_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_jsonl(self.paths.decisions)

    def append_run(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._append_jsonl(self.paths.runs, row)

    def read_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_jsonl(self.paths.runs)

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
