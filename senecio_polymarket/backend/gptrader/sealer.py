from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from .cursor import PacketCursor
from .paths import GPTraderPaths
from .schemas import (
    AUDIT_ALLOWLIST,
    OUTCOME_FUTURE_KEYS,
    PACKET_ID_PREFIX,
    PIPELINE_ALLOWLIST,
    SCHEMA_VERSION,
    TOP_LEVEL_ALLOWLIST,
    has_meaningful_value,
)


class OutcomeContaminationError(ValueError):
    code = "PACKET_REFUSED_OUTCOME_PRESENT"


class PacketSequenceError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _find_contamination(value: Any, path: tuple[str, ...] = ()) -> tuple[str, ...] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            next_path = path + (key_text,)
            if key_text.lower() in OUTCOME_FUTURE_KEYS and has_meaningful_value(child):
                return next_path
            found = _find_contamination(child, next_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            found = _find_contamination(child, path + (str(idx),))
            if found is not None:
                return found
    return None


def _scrub_future_fields(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in OUTCOME_FUTURE_KEYS:
                continue
            clean[str(key)] = _scrub_future_fields(child)
        return clean
    if isinstance(value, list):
        return [_scrub_future_fields(child) for child in value]
    return copy.deepcopy(value)


def _project_decision_time(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise TypeError("prediction source must be a dict")

    contaminated = _find_contamination(source)
    if contaminated is not None:
        location = ".".join(contaminated)
        raise OutcomeContaminationError(
            f"{OutcomeContaminationError.code}: {location}"
        )

    payload: dict[str, Any] = {}
    for key in TOP_LEVEL_ALLOWLIST:
        if key in source and key != "candle_ts":
            payload[key] = copy.deepcopy(source[key])

    audit_source = source.get("_audit") if isinstance(source.get("_audit"), dict) else {}
    candle_ts = source.get("candle_ts")
    if candle_ts is None:
        candle_ts = audit_source.get("candle_ts")
    if candle_ts is not None:
        payload["candle_ts"] = copy.deepcopy(candle_ts)

    audit: dict[str, Any] = {}
    for key in AUDIT_ALLOWLIST:
        if key in audit_source:
            audit[key] = copy.deepcopy(audit_source[key])

    pipeline_source = audit_source.get("pipeline") if isinstance(audit_source.get("pipeline"), dict) else {}
    pipeline: dict[str, Any] = {}
    for key in PIPELINE_ALLOWLIST:
        if key in pipeline_source:
            pipeline[key] = copy.deepcopy(pipeline_source[key])
    if pipeline:
        audit["pipeline"] = pipeline

    if audit:
        payload["_audit"] = audit

    return _scrub_future_fields(payload)


def _identity_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "payload": payload}


def build_sealed_packet(source: dict[str, Any], packet_seq: int) -> dict[str, Any]:
    if isinstance(packet_seq, bool) or not isinstance(packet_seq, int) or packet_seq <= 0:
        raise ValueError("packet_seq must be a positive integer")
    payload = _project_decision_time(source)
    digest = hashlib.sha256(canonical_json(_identity_envelope(payload)).encode("utf-8")).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_seq": packet_seq,
        "packet_id": f"{PACKET_ID_PREFIX}{digest[:24]}",
        "packet_hash": digest,
        **payload,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class PacketSealer:
    """Append-only T0 packet store with restart-safe monotonic sequence IDs."""

    def __init__(self, root: str | Path | None = None, paths: GPTraderPaths | None = None):
        if root is not None and paths is not None:
            raise ValueError("provide root or paths, not both")
        self.paths = paths or GPTraderPaths.from_root(root)
        self.paths.ensure_root()
        self._lock = threading.Lock()

    def _scan(self) -> tuple[dict[str, dict[str, Any]], int]:
        by_id: dict[str, dict[str, Any]] = {}
        max_seq = 0
        if not self.paths.sealed_packets.exists():
            return by_id, max_seq
        with open(self.paths.sealed_packets, "r", encoding="utf-8") as handle:
            for line_no, raw in enumerate(handle, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    packet = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise PacketSequenceError(f"invalid sealed packet JSON at line {line_no}") from exc
                seq = packet.get("packet_seq")
                packet_id = packet.get("packet_id")
                if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0 or not isinstance(packet_id, str):
                    raise PacketSequenceError(f"invalid sealed packet metadata at line {line_no}")
                if seq != max_seq + 1:
                    raise PacketSequenceError(
                        f"sealed packet sequence gap: expected {max_seq + 1}, found {seq}"
                    )
                if packet_id in by_id:
                    raise PacketSequenceError(f"duplicate packet_id at line {line_no}: {packet_id}")
                by_id[packet_id] = packet
                max_seq = seq
        return by_id, max_seq

    def _checkpoint_seq(self) -> int:
        if not self.paths.packet_seq.exists():
            return 0
        raw = self.paths.packet_seq.read_text(encoding="utf-8").strip()
        if not raw:
            return 0
        if not raw.isdigit():
            raise PacketSequenceError("packet_seq checkpoint is invalid")
        return int(raw)

    def seal(self, source: dict[str, Any]) -> dict[str, Any]:
        candidate = build_sealed_packet(source, packet_seq=1)
        packet_id = candidate["packet_id"]

        with self._lock:
            by_id, log_seq = self._scan()
            checkpoint_seq = self._checkpoint_seq()
            if checkpoint_seq > log_seq:
                raise PacketSequenceError(
                    f"packet_seq checkpoint {checkpoint_seq} is ahead of durable log {log_seq}"
                )
            existing = by_id.get(packet_id)
            if existing is not None:
                return copy.deepcopy(existing)

            next_seq = log_seq + 1
            packet = build_sealed_packet(source, packet_seq=next_seq)
            encoded = canonical_json(packet) + "\n"
            with open(self.paths.sealed_packets, "a", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _atomic_write_text(self.paths.packet_seq, f"{next_seq}\n")
            return packet

    def read_after(self, cursor: PacketCursor | str | None, limit: int = 16) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise ValueError("limit must be between 1 and 16")
        resolved = cursor if isinstance(cursor, PacketCursor) else PacketCursor.from_token(cursor)
        with self._lock:
            by_id, _ = self._scan()
            packets = sorted(by_id.values(), key=lambda packet: packet["packet_seq"])
            return [
                copy.deepcopy(packet)
                for packet in packets
                if packet["packet_seq"] > resolved.packet_seq
            ][:limit]


_DEFAULT_SEALER: PacketSealer | None = None
_DEFAULT_LOCK = threading.Lock()


def seal_prediction_t0(source: dict[str, Any]) -> dict[str, Any]:
    global _DEFAULT_SEALER
    if _DEFAULT_SEALER is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_SEALER is None:
                _DEFAULT_SEALER = PacketSealer()
    return _DEFAULT_SEALER.seal(source)
