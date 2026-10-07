from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .portfolio.persistence_paths import resolve_path


CONTRACT = "senex-prediction-persistence-receipt-v1"
DEFAULT_LEGACY_PATH = "data/portfolio/prediction_persistence_receipts.jsonl"


class PredictionPersistenceError(RuntimeError):
    """Durable source-to-authority persistence lineage is inconsistent."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PredictionPersistenceStore:
    """Append-only durable mapping from sealed T0 packet to D1 persistence."""

    def __init__(self, path: str | Path | None = None):
        resolved = resolve_path(
            "prediction_persistence_receipts.jsonl",
            DEFAULT_LEGACY_PATH,
            explicit=str(path) if path is not None else None,
            env_key="SENEX_PREDICTION_PERSISTENCE_RECEIPT_PATH",
        )
        self.path = Path(resolved)
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_no, raw in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PredictionPersistenceError(
                    f"invalid persistence receipt JSON at line {line_no}"
                ) from exc
            if not isinstance(row, dict) or row.get("contract") != CONTRACT:
                raise PredictionPersistenceError(
                    f"invalid persistence receipt contract at line {line_no}"
                )
            rows.append(row)
        return rows

    def _append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = _canonical_json(row) + "\n"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def _states(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for event in self._read():
            source_hash = str(event.get("source_packet_hash") or "")
            if len(source_hash) != 64:
                raise PredictionPersistenceError("invalid source packet hash")
            state = states.setdefault(
                source_hash,
                {
                    "source_packet_hash": source_hash,
                    "source_packet_id": event.get("source_packet_id"),
                    "source_prediction_sha256": event.get("source_prediction_sha256"),
                    "prediction": None,
                    "status": None,
                    "d1_prediction_id": None,
                    "last_reason": None,
                },
            )
            payload_hash = event.get("source_prediction_sha256")
            existing_hash = state.get("source_prediction_sha256")
            if existing_hash and payload_hash and existing_hash != payload_hash:
                raise PredictionPersistenceError(
                    f"source packet {source_hash} payload hash conflict"
                )
            if event.get("prediction") is not None:
                prediction = event.get("prediction")
                if not isinstance(prediction, dict):
                    raise PredictionPersistenceError("receipt prediction must be an object")
                computed = _sha256_json(prediction)
                if computed != payload_hash:
                    raise PredictionPersistenceError("receipt prediction payload hash mismatch")
                if state.get("prediction") is not None and state["prediction"] != prediction:
                    raise PredictionPersistenceError(
                        f"source packet {source_hash} payload conflict"
                    )
                state["prediction"] = prediction
            state["status"] = event.get("event")
            if event.get("d1_prediction_id") is not None:
                state["d1_prediction_id"] = event.get("d1_prediction_id")
            if event.get("reason") is not None:
                state["last_reason"] = event.get("reason")
        return states

    def enqueue(
        self,
        packet: dict[str, Any],
        prediction: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(packet, dict) or not isinstance(prediction, dict):
            raise PredictionPersistenceError("packet and prediction must be objects")
        source_hash = str(packet.get("packet_hash") or "")
        source_id = str(packet.get("packet_id") or "")
        if len(source_hash) != 64 or not source_id:
            raise PredictionPersistenceError("sealed packet identity is invalid")
        payload = copy.deepcopy(prediction)
        payload_hash = _sha256_json(payload)

        with self._lock:
            states = self._states()
            existing = states.get(source_hash)
            if existing is not None:
                if existing.get("source_prediction_sha256") != payload_hash:
                    raise PredictionPersistenceError(
                        f"source packet {source_hash} payload conflict"
                    )
                if existing.get("prediction") is not None and existing["prediction"] != payload:
                    raise PredictionPersistenceError(
                        f"source packet {source_hash} payload conflict"
                    )
                return copy.deepcopy(existing)

            event = {
                "contract": CONTRACT,
                "event": "ENQUEUED",
                "recorded_at": _now(),
                "source_packet_hash": source_hash,
                "source_packet_id": source_id,
                "source_prediction_sha256": payload_hash,
                "prediction": payload,
            }
            self._append(event)
            return copy.deepcopy(self._states()[source_hash])

    def mark_failed(self, source_packet_hash: str, reason: str) -> None:
        with self._lock:
            states = self._states()
            state = states.get(source_packet_hash)
            if state is None or state.get("prediction") is None:
                raise PredictionPersistenceError("cannot fail unknown source packet")
            if state.get("status") == "PERSISTED":
                return
            self._append({
                "contract": CONTRACT,
                "event": "FAILED",
                "recorded_at": _now(),
                "source_packet_hash": source_packet_hash,
                "source_packet_id": state.get("source_packet_id"),
                "source_prediction_sha256": state.get("source_prediction_sha256"),
                "reason": str(reason)[:240],
            })

    def mark_persisted(self, source_packet_hash: str, d1_prediction_id: int) -> None:
        if isinstance(d1_prediction_id, bool) or not isinstance(d1_prediction_id, int) or d1_prediction_id <= 0:
            raise PredictionPersistenceError("D1 prediction id must be a positive integer")
        with self._lock:
            states = self._states()
            state = states.get(source_packet_hash)
            if state is None or state.get("prediction") is None:
                raise PredictionPersistenceError("cannot persist unknown source packet")
            if state.get("status") == "PERSISTED":
                if int(state.get("d1_prediction_id")) != d1_prediction_id:
                    raise PredictionPersistenceError("D1 prediction id conflict")
                return
            self._append({
                "contract": CONTRACT,
                "event": "PERSISTED",
                "recorded_at": _now(),
                "source_packet_hash": source_packet_hash,
                "source_packet_id": state.get("source_packet_id"),
                "source_prediction_sha256": state.get("source_prediction_sha256"),
                "d1_prediction_id": d1_prediction_id,
            })

    def states(self) -> list[dict[str, Any]]:
        """Return reconstructed receipt states without reading outcomes."""
        with self._lock:
            states = self._states()
        return [
            copy.deepcopy(states[key])
            for key in sorted(states)
        ]

    def summary(self) -> dict[str, int]:
        states = self.states()
        persisted = sum(1 for state in states if state.get("status") == "PERSISTED")
        return {
            "expected": len(states),
            "persisted": persisted,
            "unresolved": len(states) - persisted,
        }

    def pending(self, limit: int = 4) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            states = self._states()
        pending = [
            copy.deepcopy(state)
            for state in states.values()
            if state.get("status") != "PERSISTED" and state.get("prediction") is not None
        ]
        pending.sort(
            key=lambda state: (
                str(state.get("prediction", {}).get("timestamp") or ""),
                str(state.get("source_packet_hash") or ""),
            )
        )
        return pending[:limit]
