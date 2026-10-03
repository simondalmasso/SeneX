from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - canonical SENEX runtime is Linux
    fcntl = None

from .paths import ExternalEvidencePaths
from .schema import MAX_RAW_BYTES, EvidenceCapture, ExternalEvidenceEvent
from .security import validate_public_url


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


def _fsync_directory(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _exclusive_process_lock(path: Path):
    if fcntl is None:
        raise RuntimeError("external evidence journal requires POSIX process locking")
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
        self._record_hashes: dict[str, str] = {}
        self._last_hash: str | None = None
        self._journal_signature: tuple[int, int, int, int] | None = None
        self._load_state()
        integrity = self.verify_chain()
        if not integrity.get("ok", False):
            raise RuntimeError(
                "external evidence journal integrity failure: "
                + str(integrity.get("reason") or "UNKNOWN")
            )

    def _current_journal_signature(self) -> tuple[int, int, int, int] | None:
        if not self.paths.journal.exists():
            return None
        stat = self.paths.journal.stat()
        return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))

    def _load_state(self) -> None:
        self._record_hashes.clear()
        self._last_hash = None
        if not self.paths.journal.exists():
            self._journal_signature = None
            return
        with self.paths.journal.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                event_id = str(row.get("event_id") or "")
                record_hash = str(row.get("record_hash") or "")
                if event_id and record_hash:
                    self._record_hashes[event_id] = record_hash
                if record_hash:
                    self._last_hash = record_hash
        self._journal_signature = self._current_journal_signature()

    def _put_blob(self, digest: str, raw: bytes) -> Path:
        target = self.paths.blobs / digest
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("external evidence blob hash mismatch")
            return target

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.paths.blobs,
                prefix=f".{digest}.",
                delete=False,
            ) as fh:
                temp_path = Path(fh.name)
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())

            try:
                os.link(temp_path, target)
                _fsync_directory(self.paths.blobs)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise RuntimeError("external evidence blob race/hash mismatch")
        finally:
            if temp_path is not None:
                existed = temp_path.exists()
                temp_path.unlink(missing_ok=True)
                if existed:
                    _fsync_directory(self.paths.blobs)
        return target

    def _validate_event(self, event: ExternalEvidenceEvent, raw: bytes) -> None:
        if (
            event.shadow_only is not True
            or event.decision_allowed is not False
            or event.t0_allowed is not False
        ):
            raise ValueError("external evidence shadow-only invariant violated")

        raw_bytes = bytes(raw)
        if hashlib.sha256(raw_bytes).hexdigest() != event.raw_sha256:
            raise ValueError("raw bytes do not match event raw_sha256")

        content_bytes = str(event.content).encode("utf-8")
        if hashlib.sha256(content_bytes).hexdigest() != event.content_sha256:
            raise ValueError("content does not match event content_sha256")
        if len(content_bytes) != event.content_bytes:
            raise ValueError("content byte count does not match event content_bytes")
        if len(raw_bytes) != event.raw_bytes:
            raise ValueError("raw byte count does not match event raw_bytes")

        normalized_url = validate_public_url(event.source_url)
        if normalized_url != event.source_url:
            raise ValueError("external evidence source URL is not normalized")
        canonical = EvidenceCapture(
            provider=event.provider,
            collector=event.collector,
            source_kind=event.source_kind,
            source_url=event.source_url,
            native_id=event.native_id,
            published_at=event.published_at,
            observed_at=event.observed_at,
            raw=raw_bytes,
            content=event.content,
            provider_version=event.provider_version,
            metadata=event.metadata,
        ).to_event(captured_at=event.captured_at)
        if canonical != event:
            raise ValueError("external evidence event is not canonical")

    def append(self, event: ExternalEvidenceEvent, *, raw: bytes) -> AppendResult:
        self._validate_event(event, raw)

        with self._lock:
            lock_path = self.paths.root / ".events.lock"
            with _exclusive_process_lock(lock_path):
                current_signature = self._current_journal_signature()
                if current_signature != self._journal_signature:
                    integrity = self.verify_chain()
                    if not integrity.get("ok", False):
                        raise RuntimeError(
                            "external evidence journal integrity failure before append: "
                            + str(integrity.get("reason") or "UNKNOWN")
                        )

                    # Another process or external mutation changed the journal
                    # since this instance's last successful read/write.
                    self._load_state()

                if event.event_id in self._record_hashes:
                    return AppendResult(
                        False,
                        event.event_id,
                        self._record_hashes[event.event_id],
                    )

                self._put_blob(event.raw_sha256, raw)
                row = event.to_dict()
                row["blob_relpath"] = f"blobs/{event.raw_sha256}"
                row["prev_record_hash"] = self._last_hash
                row["record_hash"] = _sha(row)

                journal_existed = self.paths.journal.exists()
                self.paths.journal.parent.mkdir(parents=True, exist_ok=True)
                with self.paths.journal.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(_canonical_json(row).decode("utf-8") + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                if not journal_existed:
                    _fsync_directory(self.paths.journal.parent)

                self._last_hash = str(row["record_hash"])
                self._record_hashes[event.event_id] = self._last_hash
                self._journal_signature = self._current_journal_signature()
                return AppendResult(True, event.event_id, self._last_hash)

    def verify_chain(self) -> dict[str, Any]:
        if not self.paths.journal.exists():
            return {"ok": True, "records": 0}
        prev: str | None = None
        count = 0
        seen: set[str] = set()
        event_fields = tuple(ExternalEvidenceEvent.__dataclass_fields__)
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
                raw_sha256 = str(row.get("raw_sha256") or "")
                if (
                    len(raw_sha256) != 64
                    or any(ch not in "0123456789abcdef" for ch in raw_sha256)
                ):
                    return {"ok": False, "records": count, "reason": "INVALID_RAW_HASH"}

                expected_relpath = f"blobs/{raw_sha256}"
                if row.get("blob_relpath") != expected_relpath:
                    return {"ok": False, "records": count, "reason": "INVALID_BLOB_PATH"}

                blob = self.paths.blobs / raw_sha256
                if not blob.is_file():
                    return {"ok": False, "records": count, "reason": "BLOB_MISSING"}
                try:
                    raw_bytes_expected = int(row.get("raw_bytes"))
                except (TypeError, ValueError):
                    return {"ok": False, "records": count, "reason": "INVALID_RAW_SIZE"}
                if (
                    raw_bytes_expected < 0
                    or raw_bytes_expected > MAX_RAW_BYTES
                    or blob.stat().st_size != raw_bytes_expected
                ):
                    return {"ok": False, "records": count, "reason": "BLOB_SIZE_MISMATCH"}

                raw_blob = blob.read_bytes()
                if hashlib.sha256(raw_blob).hexdigest() != raw_sha256:
                    return {"ok": False, "records": count, "reason": "BLOB_HASH_MISMATCH"}

                try:
                    event = ExternalEvidenceEvent(
                        **{field: row[field] for field in event_fields}
                    )
                    self._validate_event(event, raw_blob)
                except (KeyError, TypeError, ValueError):
                    return {"ok": False, "records": count, "reason": "INVALID_EVENT"}

                seen.add(event_id)
                prev = record_hash
                count += 1
        return {"ok": True, "records": count}
