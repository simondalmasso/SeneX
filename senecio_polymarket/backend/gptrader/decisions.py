from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from .cursor import CursorError, PacketCursor
from .paper_book import GPTraderPaperBook
from .sealer import PacketSealer
from .store import GPTraderStore

POLICY_ID = "GPTRADER_CHAT_V1"
MAX_BATCH = 16
_ALLOWED_DECISION_KEYS = frozenset(
    {"packet_id", "action", "reason_codes", "idempotency_key"}
)


class DecisionValidationError(ValueError):
    pass


class CursorMismatchError(RuntimeError):
    pass


class ConflictingDecisionError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class DecisionService:
    """Decision-safe GPTrader service with durable-first submission semantics."""

    def __init__(
        self,
        store: GPTraderStore,
        *,
        paper_book: Any | None = None,
        fault_hook: Callable[[str], None] | None = None,
        policy_id: str = POLICY_ID,
    ):
        self.store = store
        self.sealer = PacketSealer(paths=store.paths)
        self.paper_book = paper_book or GPTraderPaperBook(store)
        self.fault_hook = fault_hook
        self.policy_id = str(policy_id)
        self._async_lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _fault(self, event: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(event)

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._async_lock is None or self._lock_loop is not loop:
            self._async_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._async_lock

    @staticmethod
    def cursor_for_seq(packet_seq: int) -> str:
        return PacketCursor(int(packet_seq)).token

    def validate_decision(self, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise DecisionValidationError("decision must be an object")
        unknown = set(value) - _ALLOWED_DECISION_KEYS
        if unknown:
            raise DecisionValidationError(
                "forbidden decision field(s): " + ",".join(sorted(unknown))
            )
        packet_id = str(value.get("packet_id") or "").strip()
        action = str(value.get("action") or "").upper().strip()
        idem = str(value.get("idempotency_key") or "").strip()
        reasons = value.get("reason_codes")
        if not packet_id:
            raise DecisionValidationError("packet_id is required")
        if action not in {"TAKE", "ABSTAIN"}:
            raise DecisionValidationError("action must be TAKE or ABSTAIN")
        if not idem or len(idem) > 160:
            raise DecisionValidationError("bounded idempotency_key is required")
        if not isinstance(reasons, list) or len(reasons) > 8:
            raise DecisionValidationError("reason_codes must be a list of at most 8 items")
        clean_reasons: list[str] = []
        for reason in reasons:
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 64:
                raise DecisionValidationError("reason_codes contain an invalid item")
            clean_reasons.append(reason.strip())

        normalized: dict[str, Any] = {
            "packet_id": packet_id,
            "action": action,
            "reason_codes": clean_reasons,
            "idempotency_key": idem,
        }
        return normalized

    def _decision_hash(self, normalized: dict[str, Any]) -> str:
        return _sha256({"policy_id": self.policy_id, "decision": normalized})

    def _audit(self, tool: str, **metadata: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "policy_id": self.policy_id,
        }
        row.update(metadata)
        self.store.append_mcp_audit(row)

    def get_gptrader_health(self) -> dict[str, Any]:
        try:
            seal_health = self.sealer.health()
            seal_ok = bool(seal_health.get("ok"))
        except Exception as exc:
            seal_health = {"ok": False, "log_status": "ERROR", "error": type(exc).__name__}
            seal_ok = False
        cursor_state = self.store.cursor_state()
        cursor_ok = cursor_state.get("status") != "CORRUPT"
        decision_health = self.store.decision_log_health()
        decision_ok = bool(decision_health.get("ok"))
        cursor_seq = cursor_state.get("packet_seq")
        result = {
            "ready": seal_ok and cursor_ok and decision_ok,
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "schema_version": "gptrader.decision.v1",
            "policy_id": self.policy_id,
            "volume_ready": self.store.paths.root.is_dir(),
            "cursor_ready": cursor_ok,
            "cursor_status": cursor_state.get("status"),
            "cursor": (
                self.cursor_for_seq(int(cursor_seq))
                if isinstance(cursor_seq, int)
                else None
            ),
            "packet_count": len(self.store.read_packets()),
            "seal_health": seal_health,
            "decision_log_health": decision_health,
        }
        self._audit("get_gptrader_health", ready=result["ready"])
        return result

    def get_prediction_batch(
        self,
        cursor: str | None,
        limit: int = 8,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH:
            raise DecisionValidationError("limit must be between 1 and 16")
        current = self.store.cursor_seq()
        if cursor in (None, ""):
            start = PacketCursor(current)
        else:
            try:
                start = PacketCursor.from_token(cursor)
            except CursorError as exc:
                raise CursorMismatchError("CURSOR_INVALID") from exc
            if start.packet_seq != current:
                raise CursorMismatchError("CURSOR_MISMATCH")

        packets = self.sealer.read_after(start, limit=limit)
        next_seq = packets[-1]["packet_seq"] if packets else start.packet_seq
        next_cursor = self.cursor_for_seq(next_seq)
        has_more = bool(self.sealer.read_after(PacketCursor(next_seq), limit=1))
        packet_ids = [packet["packet_id"] for packet in packets]
        batch_id = _sha256(
            {
                "policy_id": self.policy_id,
                "cursor_in": start.token,
                "next_cursor": next_cursor,
                "packet_ids": packet_ids,
            }
        )[:32]
        result = {
            "batch_id": batch_id,
            "cursor_in": start.token,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "packets": packets,
        }
        self._audit("get_prediction_batch", count=len(packets), batch_id=batch_id)
        return result

    def get_gptrader_state(self) -> dict[str, Any]:
        raw = self.paper_book.state(public=False)
        risk = raw.get("risk_state") if isinstance(raw.get("risk_state"), dict) else {}
        result = {
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "orders_enabled": False,
            "open_count": raw.get("open_count"),
            "kill_switch_active": risk.get("kill_switch_active"),
            "last_run_id": raw.get("last_run_id"),
            "cursor": self.cursor_for_seq(self.store.cursor_seq()),
        }
        self._audit("get_gptrader_state")
        return result

    def _existing_for(self, normalized: dict[str, Any]) -> dict[str, Any] | None:
        return self.store.find_decision(self.policy_id, normalized["packet_id"])

    def _assert_existing_compatible(
        self,
        normalized: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> None:
        if existing is None:
            return
        if existing.get("decision_hash") != self._decision_hash(normalized):
            raise ConflictingDecisionError("CONFLICT_ALREADY_DECIDED")

    def _packets_for_submit(
        self,
        start: PacketCursor,
        normalized: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        packets = self.sealer.read_after(start, limit=len(normalized))
        if len(packets) != len(normalized):
            raise CursorMismatchError("BATCH_LENGTH_MISMATCH")
        expected = [packet["packet_id"] for packet in packets]
        supplied = [decision["packet_id"] for decision in normalized]
        if supplied != expected:
            raise CursorMismatchError("BATCH_NOT_CONTIGUOUS_PREFIX")
        return packets

    async def submit_paper_decisions(
        self,
        run_id: str,
        cursor: str,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self._lock():
            return await self._submit_locked(run_id, cursor, decisions)

    async def _submit_locked(
        self,
        run_id: str,
        cursor: str,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id or len(run_id) > 160:
            raise DecisionValidationError("bounded run_id is required")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= MAX_BATCH:
            raise DecisionValidationError("decisions must contain 1..16 items")
        normalized = [self.validate_decision(item) for item in decisions]
        if len({item["packet_id"] for item in normalized}) != len(normalized):
            raise DecisionValidationError("duplicate packet_id in batch")

        try:
            start = PacketCursor.from_token(cursor)
        except CursorError as exc:
            raise CursorMismatchError("CURSOR_INVALID") from exc

        existing = [self._existing_for(item) for item in normalized]
        for item, prior in zip(normalized, existing):
            self._assert_existing_compatible(item, prior)
        all_existing = all(prior is not None for prior in existing)
        current = self.store.cursor_seq()

        if start.packet_seq != current:
            if all_existing:
                seq_by_id = {
                    row.get("packet_id"): int(row.get("packet_seq") or 0)
                    for row in self.store.read_packets()
                }
                required = max(seq_by_id.get(item["packet_id"], 0) for item in normalized)
                if required > 0 and current >= required:
                    self._audit("submit_paper_decisions", duplicate=True, count=len(normalized))
                    return {
                        "applied": 0,
                        "duplicate": True,
                        "recovered": False,
                        "cursor": self.cursor_for_seq(current),
                    }
            raise CursorMismatchError("CURSOR_MISMATCH")

        packets = self._packets_for_submit(start, normalized)

        self._fault("before_decision_commit")
        committed_now = 0
        for item, prior in zip(normalized, existing):
            if prior is not None:
                continue
            decision_hash = self._decision_hash(item)
            self.store.append_decision(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    "policy_id": self.policy_id,
                    **item,
                    "decision_hash": decision_hash,
                    "commit_state": "DURABLE_BEFORE_PAPER",
                }
            )
            committed_now += 1
        self._fault("after_decision_commit")

        results: list[dict[str, Any]] = []
        for packet, item in zip(packets, normalized):
            payload = {**item, "policy_id": self.policy_id}
            result = await self.paper_book.apply_decision(
                packet,
                payload,
                run_id=run_id,
                persist_decision=False,
            )
            results.append(result)

        self._fault("after_paper_apply_before_cursor")
        next_seq = int(packets[-1]["packet_seq"])
        next_cursor = self.cursor_for_seq(next_seq)
        self.store.set_cursor_seq(next_seq, next_cursor)
        recovered = all_existing and committed_now == 0
        self._audit(
            "submit_paper_decisions",
            count=len(normalized),
            duplicate=False,
            recovered=recovered,
            cursor_seq=next_seq,
        )
        return {
            "applied": len(normalized),
            "duplicate": False,
            "recovered": recovered,
            "cursor": next_cursor,
            "results": results,
        }

    def read_settlement_after_decision(
        self,
        packet_id: str,
        reader: Callable[[dict[str, Any]], Any],
    ) -> Any:
        decision = self.store.find_decision(self.policy_id, str(packet_id))
        if decision is None:
            raise RuntimeError("DECISION_NOT_COMMITTED")
        packet = self.store.find_packet(str(packet_id))
        if packet is None:
            raise KeyError("PACKET_NOT_FOUND")
        return reader(packet)
