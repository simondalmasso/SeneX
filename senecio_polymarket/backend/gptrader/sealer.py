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

PACKET_TARGET_BYTES = 4096
PACKET_HARD_MAX_BYTES = 8192
_RECOVERY_MARKER_KEY = "_gptrader_recovery_v1"


class OutcomeContaminationError(ValueError):
    code = "PACKET_REFUSED_OUTCOME_PRESENT"


class PacketSequenceError(RuntimeError):
    pass


class PacketSizeError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


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


def _extract_provenance(audit: dict[str, Any]) -> dict[str, Any] | None:
    replay = audit.get("decision_replay_v1")
    if not isinstance(replay, dict):
        return None
    keys = (
        "version",
        "captured_at",
        "snapshot_hash",
        "query_observed_at_epoch",
        "feature_source_identity",
        "runtime_provenance",
        "learning_source_evidence_hash",
        "effective_weights_hash",
        "code_hash",
        "config_hash",
    )
    out = {key: copy.deepcopy(replay[key]) for key in keys if key in replay}
    return out or None


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact = copy.deepcopy(payload)
    audit = compact.get("_audit") if isinstance(compact.get("_audit"), dict) else None
    if not audit:
        return compact

    provenance = _extract_provenance(audit)
    if provenance is not None:
        audit["provenance_v1"] = provenance

    audit.pop("decision_replay_v1", None)
    audit.pop("external_markets_v1", None)
    return compact


def _identity_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "payload": payload}


def _packet_from_payload(payload: dict[str, Any], packet_seq: int) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_bytes(_identity_envelope(payload))).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_seq": packet_seq,
        "packet_id": f"{PACKET_ID_PREFIX}{digest[:24]}",
        "packet_hash": digest,
        **payload,
    }


def build_sealed_packet(source: dict[str, Any], packet_seq: int) -> dict[str, Any]:
    if isinstance(packet_seq, bool) or not isinstance(packet_seq, int) or packet_seq <= 0:
        raise ValueError("packet_seq must be a positive integer")

    payload = _project_decision_time(source)
    packet = _packet_from_payload(payload, packet_seq)
    if len(_canonical_bytes(packet)) > PACKET_TARGET_BYTES:
        payload = _compact_payload(payload)
        packet = _packet_from_payload(payload, packet_seq)

    size = len(_canonical_bytes(packet))
    if size > PACKET_HARD_MAX_BYTES:
        raise PacketSizeError(
            f"sealed packet exceeds hard limit: {size}>{PACKET_HARD_MAX_BYTES}"
        )
    return packet


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _recovery_marker(raw_tail: bytes, line_no: int) -> dict[str, Any]:
    return {
        _RECOVERY_MARKER_KEY: {
            "kind": "TORN_TAIL",
            "line_no": line_no,
            "sha256": hashlib.sha256(raw_tail).hexdigest(),
            "bytes": len(raw_tail),
        }
    }


def _marker_matches(packet: Any, raw_tail: bytes, line_no: int) -> bool:
    if not isinstance(packet, dict):
        return False
    marker = packet.get(_RECOVERY_MARKER_KEY)
    if not isinstance(marker, dict):
        return False
    return (
        marker.get("kind") == "TORN_TAIL"
        and marker.get("line_no") == line_no
        and marker.get("sha256") == hashlib.sha256(raw_tail).hexdigest()
        and marker.get("bytes") == len(raw_tail)
    )


class PacketSealer:
    """Append-only T0 packet store with restart-safe monotonic sequence IDs."""

    def __init__(self, root: str | Path | None = None, paths: GPTraderPaths | None = None):
        if root is not None and paths is not None:
            raise ValueError("provide root or paths, not both")
        self.paths = paths or GPTraderPaths.from_root(root)
        self.paths.ensure_root()
        self._lock = threading.Lock()
        self._last_health = {
            "ok": True,
            "log_status": "OK",
            "recovered_torn_tails": 0,
            "pending_torn_tail": False,
        }

    def _scan(self) -> tuple[dict[str, dict[str, Any]], int, dict[str, Any] | None]:
        by_id: dict[str, dict[str, Any]] = {}
        max_seq = 0
        recovered = 0
        pending_torn: dict[str, Any] | None = None
        if not self.paths.sealed_packets.exists():
            self._last_health = {
                "ok": True,
                "log_status": "OK",
                "recovered_torn_tails": 0,
                "pending_torn_tail": False,
            }
            return by_id, max_seq, pending_torn

        raw_file = self.paths.sealed_packets.read_bytes()
        lines = raw_file.splitlines(keepends=True)
        idx = 0
        while idx < len(lines):
            line_no = idx + 1
            raw_line = lines[idx]
            terminated = raw_line.endswith((b"\n", b"\r"))
            content = raw_line.rstrip(b"\r\n")
            if not content.strip():
                idx += 1
                continue
            try:
                packet = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                is_last = idx == len(lines) - 1
                if is_last and not terminated:
                    pending_torn = {"raw": content, "line_no": line_no}
                    self._last_health = {
                        "ok": True,
                        "log_status": "TORN_TAIL_PENDING_RECOVERY",
                        "recovered_torn_tails": recovered,
                        "pending_torn_tail": True,
                    }
                    return by_id, max_seq, pending_torn

                if idx + 1 < len(lines):
                    marker_content = lines[idx + 1].rstrip(b"\r\n")
                    try:
                        marker = json.loads(marker_content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        marker = None
                    if _marker_matches(marker, content, line_no):
                        recovered += 1
                        idx += 2
                        continue

                self._last_health = {
                    "ok": False,
                    "log_status": "CORRUPT",
                    "recovered_torn_tails": recovered,
                    "pending_torn_tail": False,
                    "error_line": line_no,
                }
                raise PacketSequenceError(
                    f"invalid sealed packet JSON at line {line_no}"
                ) from exc

            if isinstance(packet, dict) and _RECOVERY_MARKER_KEY in packet:
                raise PacketSequenceError(f"orphan recovery marker at line {line_no}")

            seq = packet.get("packet_seq") if isinstance(packet, dict) else None
            packet_id = packet.get("packet_id") if isinstance(packet, dict) else None
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
            idx += 1

        self._last_health = {
            "ok": True,
            "log_status": "RECOVERED_TORN_TAIL" if recovered else "OK",
            "recovered_torn_tails": recovered,
            "pending_torn_tail": False,
        }
        return by_id, max_seq, pending_torn

    def _checkpoint_seq(self) -> int:
        if not self.paths.packet_seq.exists():
            return 0
        raw = self.paths.packet_seq.read_text(encoding="utf-8").strip()
        if not raw:
            return 0
        if not raw.isdigit():
            raise PacketSequenceError("packet_seq checkpoint is invalid")
        return int(raw)

    def _append_torn_tail_marker(self, pending: dict[str, Any]) -> None:
        marker = _recovery_marker(pending["raw"], pending["line_no"])
        with open(self.paths.sealed_packets, "ab") as handle:
            handle.write(b"\n")
            handle.write(_canonical_bytes(marker))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def seal(self, source: dict[str, Any]) -> dict[str, Any]:
        candidate = build_sealed_packet(source, packet_seq=1)
        packet_id = candidate["packet_id"]

        with self._lock:
            by_id, log_seq, pending = self._scan()
            checkpoint_seq = self._checkpoint_seq()
            if checkpoint_seq > log_seq:
                raise PacketSequenceError(
                    f"packet_seq checkpoint {checkpoint_seq} is ahead of durable log {log_seq}"
                )
            existing = by_id.get(packet_id)
            if existing is not None:
                return copy.deepcopy(existing)

            if pending is not None:
                self._append_torn_tail_marker(pending)
                recovered_before = self._last_health.get("recovered_torn_tails", 0)
                self._last_health = {
                    "ok": True,
                    "log_status": "RECOVERED_TORN_TAIL",
                    "recovered_torn_tails": recovered_before + 1,
                    "pending_torn_tail": False,
                }

            next_seq = log_seq + 1
            packet = build_sealed_packet(source, packet_seq=next_seq)
            encoded = _canonical_bytes(packet) + b"\n"
            with open(self.paths.sealed_packets, "ab") as handle:
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
            by_id, _, _ = self._scan()
            packets = sorted(by_id.values(), key=lambda packet: packet["packet_seq"])
            return [
                copy.deepcopy(packet)
                for packet in packets
                if packet["packet_seq"] > resolved.packet_seq
            ][:limit]

    def health(self) -> dict[str, Any]:
        with self._lock:
            self._scan()
            return copy.deepcopy(self._last_health)


_DEFAULT_SEALER: PacketSealer | None = None
_DEFAULT_LOCK = threading.Lock()


def seal_prediction_t0(source: dict[str, Any]) -> dict[str, Any]:
    global _DEFAULT_SEALER
    if _DEFAULT_SEALER is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_SEALER is None:
                _DEFAULT_SEALER = PacketSealer()
    return _DEFAULT_SEALER.seal(source)
