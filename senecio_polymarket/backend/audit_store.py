"""
SENECIO ORACLE — Audit Store
============================
Append-only JSONL persistence for every canonical event.
- Daily-rotated files under data/audit/YYYY-MM-DD.jsonl
- Synchronous writes (small batches) — safe for moderate throughput
- Replayable from any timestamp for backtest / debugging
"""
from __future__ import annotations

import io
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import BaseEvent, to_log_line, from_log_line


class AuditStore:
    def __init__(self, root: str | Path = "data/audit"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._buffer_size = 64
        self._current_day: str | None = None
        self._current_fh: io.TextIOWrapper | None = None

    def _day_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _file_path(self, day: str) -> Path:
        return self.root / f"{day}.jsonl"

    def _ensure_open(self) -> None:
        day = self._day_str()
        if day != self._current_day:
            if self._current_fh:
                self._current_fh.flush()
                self._current_fh.close()
            self._current_day = day
            self._current_fh = open(self._file_path(day), "a", encoding="utf-8")

    def append(self, ev: BaseEvent) -> None:
        line = to_log_line(ev)
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= self._buffer_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        self._ensure_open()
        assert self._current_fh is not None
        self._current_fh.write("\n".join(self._buffer) + "\n")
        self._current_fh.flush()
        self._buffer.clear()

    # ---- replay ----
    def iter_events(self, day: str | None = None) -> Iterator[BaseEvent]:
        if day:
            files = [self._file_path(day)]
        else:
            files = sorted(self.root.glob("*.jsonl"))
        for fp in files:
            if not fp.exists():
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield from_log_line(line)
                    except Exception:
                        continue

    def stats(self) -> dict:
        files = sorted(self.root.glob("*.jsonl"))
        total_bytes = sum(f.stat().st_size for f in files if f.exists())
        return {
            "audit_files": len(files),
            "audit_total_bytes": total_bytes,
            "audit_root": str(self.root),
            "latest_day": files[-1].stem if files else None,
        }
