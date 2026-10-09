"""Fixture/offline only append-only M17 evidence store.

No network, credentials, wallet, orders, production imports, or automated repair.
Local hash chains detect partial manipulation, not a total rewrite by an attacker
with filesystem control; an independent remote anchor is REQUIRED for promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


class IntegrityError(ValueError):
    """Tamper, crash orphan, broken chain or duplicate evidence slot."""


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise IntegrityError("non-canonical record") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_constant(value):
    raise IntegrityError("non-finite JSON numeric literal: "+value)


class AppendOnlyEvidence:
    """Durable local receipt chain with hard-fail recovery; NEVER a source oracle.

    Crash after writing a blob but before committing the journal creates an
    orphan: restart fails closed; no auto-backfill or deletion.
    """

    ALLOWED_KINDS = frozenset({
        "T0_SLOT", "T1_OBSERVATION", "MISSED_WINDOW", "GAP", "RECONNECT",
    })
    ALLOWED_ATTACHMENTS = frozenset({
        "market_metadata", "market_rule", "book_yes", "book_no", "fee_yes",
        "fee_no", "senex_signal", "twap60", "t1_original", "raw_frame",
    })

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if self.root.is_symlink():
            raise IntegrityError("store root symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / "blobs"
        if self.blobs.is_symlink():
            raise IntegrityError("blobs directory symlink")
        self.blobs.mkdir(exist_ok=True)
        self.journal = self.root / "journal.jsonl"
        self.lock = self.root / "writer.lock"
        if self.journal.is_symlink() or self.lock.is_symlink():
            raise IntegrityError("journal or lock symlink")
        if self.lock.exists():
            raise IntegrityError("stale writer lock: manual offline review required")
        self.verify()

    def verify(self) -> list[dict]:
        if self.journal.is_symlink() or self.blobs.is_symlink():
            raise IntegrityError("tampered path")
        b = self.journal.read_bytes() if self.journal.exists() else b""
        if b and not b.endswith(b"\n"):
            raise IntegrityError("torn journal tail")
        records, expected_blobs = [], set()
        last_hash = "0" * 64
        slots = set()
        t0_seen = set()
        for line in b.splitlines():
            try:
                item = json.loads(line, parse_constant=_reject_constant)
            except (ValueError, UnicodeError, TypeError) as exc:
                raise IntegrityError("invalid journal JSON") from exc
            if not isinstance(item, dict) or _json_bytes(item) != line:
                raise IntegrityError("noncanonical journal line")
            chain_hash = item.get("chain_hash")
            payload = {k:v for k,v in item.items() if k != "chain_hash"}
            if (item.get("seq") != len(records)+1 or
                    item.get("prev_hash") != last_hash or
                    chain_hash != _sha(_json_bytes(payload))):
                raise IntegrityError("broken sequential SHA256 chain")
            if item.get("phase") != "FIXTURE_OFFLINE":
                raise IntegrityError("non-fixture evidence forbidden")
            kind, slot = item.get("kind"), item.get("slot_key")
            if kind not in self.ALLOWED_KINDS:
                raise IntegrityError("unknown journal record kind")
            if kind in ("T0_SLOT","MISSED_WINDOW"):
                if slot in slots or not slot:
                    raise IntegrityError("duplicate original opportunity")
                slots.add(slot)
                if kind=="T0_SLOT": t0_seen.add(slot)
            if kind=="T1_OBSERVATION":
                if slot not in t0_seen or ("T1_OBSERVATION",slot) in slots:
                    raise IntegrityError("T1 missing T0 or duplicate")
                slots.add(("T1_OBSERVATION",slot))
            arts = item.get("artifacts")
            if not isinstance(arts, dict):
                raise IntegrityError("invalid artifact manifest")
            for name, info in arts.items():
                if name not in self.ALLOWED_ATTACHMENTS or not isinstance(info,dict):
                    raise IntegrityError("unknown artifact")
                filename, digest = info.get("file"), info.get("sha256")
                if (not isinstance(filename,str) or not re.fullmatch(
                    r"[0-9]{8}_[a-z0-9_]+_[a-f0-9]{20}\.raw",filename)):
                    raise IntegrityError("unsafe artifact filename")
                if (not isinstance(digest,str) or not re.fullmatch("[a-f0-9]{64}",digest) or
                        not filename.startswith(f'{item["seq"]:08d}_'+name+'_') or
                        filename[-24:-4] != digest[:20]):
                    raise IntegrityError("incorrect artifact identity")
                path = self.blobs / filename
                if path.is_symlink() or not path.is_file():
                    raise IntegrityError("missing or symlinked blob")
                raw = path.read_bytes()
                if _sha(raw) != digest or len(raw) != info.get("bytes"):
                    raise IntegrityError("original blob bytes changed")
                expected_blobs.add(filename)
            last_hash=chain_hash
            records.append(item)
        present = {p.name for p in self.blobs.iterdir()}
        if expected_blobs != present:
            raise IntegrityError("crash orphan, unexpected or deleted original blob")
        return records

    def append(self, *, kind: str, slot_key: str, now_ms: int,
               artifacts: dict[str, bytes], attrs: dict) -> dict:
        if kind not in self.ALLOWED_KINDS:
            raise IntegrityError("unapproved evidence type")
        if type(now_ms) is not int or now_ms < 0:
            raise IntegrityError("invalid monotonic timestamp")
        if not isinstance(slot_key,str) or not isinstance(artifacts,dict) or not isinstance(attrs,dict):
            raise IntegrityError("invalid record inputs")
        if not set(artifacts).issubset(self.ALLOWED_ATTACHMENTS):
            raise IntegrityError("nonallowlisted attachment")
        for k,raw in artifacts.items():
            if not isinstance(raw,bytes) or not raw or len(raw)>4_000_000:
                raise IntegrityError("original source bytes required and bounded: "+k)
        if set(attrs).intersection(("seq","kind","slot_key","now_ms","phase",
                                     "artifacts","prev_hash","chain_hash")):
            raise IntegrityError("cannot override authoritative manifest fields")
        fd = None
        try:
            fd=os.open(self.lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.close(fd);fd=None
        except FileExistsError as exc:
            raise IntegrityError("concurrent writer or crash lock") from exc
        try:
            records=self.verify()
            if kind in ("T0_SLOT","MISSED_WINDOW"):
                if any(x["slot_key"]==slot_key and x["kind"] in (
                   "T0_SLOT","MISSED_WINDOW") for x in records):
                    raise IntegrityError("duplicate T0 or missed opportunity")
            if kind=="T1_OBSERVATION":
                if not any(x["kind"]=="T0_SLOT" and x["slot_key"]==slot_key for x in records):
                    raise IntegrityError("T1 without original T0")
                if any(x["kind"]=="T1_OBSERVATION" and x["slot_key"]==slot_key for x in records):
                    raise IntegrityError("duplicate T1")
            seq=len(records)+1
            manifest={}
            for name,raw in sorted(artifacts.items()):
                digest=_sha(raw)
                filename=f"{seq:08d}_{name}_{digest[:20]}.raw"
                path=self.blobs/filename
                blob_fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(blob_fd,"wb") as stream:
                    stream.write(raw);stream.flush();os.fsync(stream.fileno())
                manifest[name]={"file":filename,"sha256":digest,"bytes":len(raw)}
            entry={"seq":seq,"kind":kind,"slot_key":slot_key,"now_ms":now_ms,
                   "phase":"FIXTURE_OFFLINE","artifacts":manifest,
                   "prev_hash":records[-1]["chain_hash"] if records else "0"*64,
                   **attrs}
            entry["chain_hash"]=_sha(_json_bytes(entry))
            journal_fd=os.open(self.journal,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
            with os.fdopen(journal_fd,"ab") as f:
                f.write(_json_bytes(entry)+b"\n")
                f.flush();os.fsync(f.fileno())
            self.verify()
            return entry
        finally:
            self.lock.unlink(missing_ok=True)

    def read_bytes(self, info: dict) -> bytes:
        self.verify()
        if not isinstance(info,dict):
            raise IntegrityError("unvalidated blob reference")
        filename=info.get("file")
        if not isinstance(filename,str) or not re.fullmatch(
            r"[0-9]{8}_[a-z0-9_]+_[a-f0-9]{20}\.raw",filename):
            raise IntegrityError("unsafe blob reference")
        return (self.blobs/filename).read_bytes()
