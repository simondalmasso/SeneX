from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import ExperimentRecord
from .replay import semantic_key


class DuplicateExperimentError(ValueError):
    pass


class ExperimentLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def find_duplicate(self, record: ExperimentRecord) -> dict[str, Any] | None:
        wanted = record.record_hash()
        for row in self._rows():
            if row.get("record_hash") == wanted:
                return row
        return None

    def append(self, record: ExperimentRecord) -> dict[str, Any]:
        duplicate = self.find_duplicate(record)
        if duplicate is not None:
            raise DuplicateExperimentError(record.hypothesis_id)
        payload = record.payload()
        payload["record_hash"] = record.record_hash()
        payload["semantic_key"] = semantic_key(
            hypothesis_id=record.hypothesis_id,
            candidate_definition=record.candidate_definition,
            baseline_definition=record.baseline_definition,
            horizon=record.horizon,
            regime=record.regime,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
        return payload

    def find_semantic_duplicate(
        self,
        *,
        hypothesis_id: str,
        candidate_definition: str,
        baseline_definition: str,
        horizon: str,
        regime: str,
    ) -> dict[str, Any] | None:
        wanted = semantic_key(
            hypothesis_id=hypothesis_id,
            candidate_definition=candidate_definition,
            baseline_definition=baseline_definition,
            horizon=horizon,
            regime=regime,
        )
        for row in reversed(self._rows()):
            if row.get("semantic_key") == wanted:
                return row
        return None

    def replay(self, *, hypothesis_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._rows()
        if hypothesis_id is None:
            return rows
        return [row for row in rows if row.get("hypothesis_id") == hypothesis_id]
