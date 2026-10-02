from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ExternalEvidencePaths
from .schema import ExternalEvidenceEvent


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class AppendResult:
    appended: bool
    event_id: str
    record_hash: str | None


class ExternalEvidenceJournal:
    """Single-writer append-only external evidence journal with hash chaining."""

    def __init__(self, paths: ExternalEvidencePaths | None = None):
        self.paths = paths or ExternalEvidencePaths.default()
        self.paths.ensure()
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        self._last_hash: str | None = None
        self._load_state()

    def _load_state(self) -> None:
        if not self.paths.journal.exists():
            return
        with self.paths.journal.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                event_id = str(row.get("event_id") or "")
                record_hash = str(row.get("record_hash") or "")
                if event_id:
                    self._ids.add(event_id)
                if record_hash:
                    self._last_hash = record_hash

    def _put_blob(self, digest: str, raw: bytes) -> Path:
        target = self.paths.blobs / digest
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("external evidence blob hash mismatch")
            return target
        fd: int | None = None
        try:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as fh:
                fd = None
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("external evidence blob race/hash mismatch")
        finally:
            if fd is not None:
                os.close(fd)
        return target

    def append(self, event: ExternalEvidenceEvent, *, raw: bytes) -> AppendResult:
        if hashlib.sha256(raw).hexdigest() != event.raw_sha256:
            raise ValueError("raw bytes do not match event raw_sha256")

        with self._lock:
            if event.event_id in self._ids:
                return AppendResult(False, event.event_id, self._last_hash)

            self._put_blob(event.raw_sha256, raw)
            row = event.to_dict()
            row["blob_relpath"] = f"blobs/{event.raw_sha256}"
            row["prev_record_hash"] = self._last_hash
            row["record_hash"] = _sha(row)

            self.paths.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.paths.journal.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(_canonical_json(row).decode("utf-8") + "\n")
                fh.flush()
                os.fsync(fh.fileno())

            self._ids.add(event.event_id)
            self._last_hash = str(row["record_hash"])
            return AppendResult(True, event.event_id, self._last_hash)

    def verify_chain(self) -> dict[str, Any]:
        if not self.paths.journal.exists():
            return {"ok": True, "records": 0}
        prev: str | None = None
        count = 0
        seen: set[str] = set()
        with self.paths.journal.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                record_hash = str(row.get("record_hash") or "")
                event_id = str(row.get("event_id") or "")
                if not record_hash or not event_id or event_id in seen:
                    return {"ok": False, "records": count, "reason": "INVALID_ID_OR_HASH"}
                if row.get("prev_record_hash") != prev:
                    return {"ok": False, "records": count, "reason": "CHAIN_LINK_MISMATCH"}
                unsigned = dict(row)
                unsigned.pop("record_hash", None)
                if _sha(unsigned) != record_hash:
                    return {"ok": False, "records": count, "reason": "RECORD_HASH_MISMATCH"}
                blob = self.paths.root / str(row.get("blob_relpath") or "")
                if not blob.is_file():
                    return {"ok": False, "records": count, "reason": "BLOB_MISSING"}
                if hashlib.sha256(blob.read_bytes()).hexdigest() != str(row.get("raw_sha256") or ""):
                    return {"ok": False, "records": count, "reason": "BLOB_HASH_MISMATCH"}
                seen.add(event_id)
                prev = record_hash
                count += 1
        return {"ok": True, "records": count}
