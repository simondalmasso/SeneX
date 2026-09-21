from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

ARQ3_CONTRACT_SOURCE_SHA = "92086c3d3227afe524fe86fa983cae1d291a1168"
ARQ3_CONTRACT_SOURCE_PATH = "execution_organelle/contract.py"
ARQ3_CAPTURE_POLICY_SOURCE_SHA = ARQ3_CONTRACT_SOURCE_SHA
ARQ3_JEV_PACKET_SOURCE_SHA = ARQ3_CONTRACT_SOURCE_SHA
ARQ3_JEV_PACKET_SOURCE_PATH = "execution_organelle/jev_packet.py"
SCHEMA_VERSION = "execution_evidence_v1"
MAX_CAPTURE_LATENESS_MS = 2000
_HASH_LEN = 64


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _has_l2(book: dict[str, Any]) -> bool:
    return bool(book.get("bids")) and bool(book.get("asks"))


def _fee_proven(schedule: Any) -> bool:
    if not isinstance(schedule, dict):
        return False
    status = str(schedule.get("status") or "").upper()
    if status == "FEE_FREE":
        return True
    return (
        status == "PROVEN"
        and schedule.get("rate") is not None
        and str(schedule.get("exponent")) == "1"
        and schedule.get("taker_only") is True
    )


def validate_execution_record(record: dict[str, Any]) -> list[str]:
    """Pinned local mirror of ARQ3 validate_execution_record at source SHA above."""
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION_INVALID")

    required_ids = (
        "opportunity_id",
        "event_id",
        "condition_id",
        "token_id",
        "market_slug",
    )
    for key in required_ids:
        if not record.get(key):
            errors.append(f"{key.upper()}_MISSING")

    times: dict[str, int] = {}
    for key in ("event_time", "source_time", "receipt_time", "decision_time", "arrival_time"):
        value = record.get(key)
        if value is None:
            errors.append(f"{key.upper()}_MISSING")
            continue
        try:
            times[key] = int(value)
        except (TypeError, ValueError):
            errors.append(f"{key.upper()}_INVALID")

    if len(times) == 5:
        if times["source_time"] > times["receipt_time"]:
            errors.append("SOURCE_TIME_AFTER_RECEIPT")
        if times["receipt_time"] > times["decision_time"]:
            errors.append("RECEIPT_AFTER_DECISION")
        if times["decision_time"] > times["arrival_time"]:
            errors.append("DECISION_AFTER_ARRIVAL")
        if times["event_time"] > times["decision_time"]:
            errors.append("EVENT_FROM_FUTURE")

    for label, time_key in (("DECISION", "decision_time"), ("ARRIVAL", "arrival_time")):
        book = record.get(f"{label.lower()}_book")
        if not isinstance(book, dict):
            errors.append(f"{label}_BOOK_MISSING")
            continue
        if not _has_l2(book):
            errors.append(f"{label}_BOOK_MISSING_FULL_L2")
        for field in (
            "source",
            "source_timestamp",
            "received_timestamp",
            "sha256",
            "tick_size",
            "minimum_order_size",
        ):
            if book.get(field) is None:
                errors.append(f"{label}_BOOK_{field.upper()}_MISSING")
        digest = str(book.get("sha256") or "")
        if len(digest) != _HASH_LEN:
            errors.append(f"{label}_BOOK_HASH_INVALID")
        if not _fee_proven(book.get("fee_schedule")):
            errors.append(f"{label}_FEE_SCHEDULE_UNPROVEN")
        if time_key in times and book.get("source_timestamp") is not None:
            try:
                if int(book["source_timestamp"]) > times[time_key]:
                    errors.append(f"{label}_BOOK_FROM_FUTURE")
            except (TypeError, ValueError):
                errors.append(f"{label}_BOOK_SOURCE_TIMESTAMP_INVALID")
        if time_key in times and book.get("received_timestamp") is not None:
            try:
                if int(book["received_timestamp"]) > times[time_key]:
                    errors.append(f"{label}_BOOK_RECEIVED_AFTER_{label}_TIME")
            except (TypeError, ValueError):
                errors.append(f"{label}_BOOK_RECEIVED_TIMESTAMP_INVALID")

    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        errors.append("CANDIDATE_MISSING")
    else:
        if str(candidate.get("side") or "").upper() not in {"BUY", "SELL"}:
            errors.append("CANDIDATE_SIDE_INVALID")
        if not candidate.get("outcome"):
            errors.append("CANDIDATE_OUTCOME_MISSING")
        if (
            candidate.get("requested_notional_usd") is None
            and candidate.get("requested_shares") is None
        ):
            errors.append("CANDIDATE_SIZE_MISSING")

    execution = record.get("execution")
    execution_fields = (
        "executable",
        "reject_reason",
        "executable_shares",
        "fill_fraction",
        "levels_consumed",
        "best_price",
        "vwap",
        "spread_cost_usd",
        "depth_slippage_usd",
        "fees_usd",
        "rebate_usd_if_proven",
        "total_entry_cost_usd",
    )
    if not isinstance(execution, dict) or execution.get("executable") is None:
        errors.append("EXECUTION_MISSING")
    else:
        for field in execution_fields:
            if field not in execution:
                errors.append(f"EXECUTION_{field.upper()}_MISSING")
        if execution.get("executable") is False and not execution.get("reject_reason"):
            errors.append("REJECTION_REASON_MISSING")
        try:
            rebate = Decimal(str(execution.get("rebate_usd_if_proven") or "0"))
        except Exception:
            errors.append("REBATE_VALUE_INVALID")
            rebate = Decimal("0")
        if rebate > 0:
            schedule = (record.get("arrival_book") or {}).get("fee_schedule") or {}
            if str(schedule.get("rebate_status") or "").upper() != "PROVEN":
                errors.append("REBATE_UNPROVEN")

    raw = record.get("raw_evidence")
    if not isinstance(raw, dict):
        errors.append("RAW_EVIDENCE_MISSING")
    else:
        for label in ("decision", "arrival"):
            raw_book = raw.get(f"{label}_book")
            normalized = record.get(f"{label}_book") or {}
            if not isinstance(raw_book, dict):
                errors.append(f"{label.upper()}_RAW_EVIDENCE_MISSING")
                continue
            if _sha256(raw_book) != normalized.get("sha256"):
                errors.append(f"{label.upper()}_RAW_HASH_MISMATCH")

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("PROVENANCE_MISSING")
    else:
        for field in ("code_hash", "config_hash", "policy_hash"):
            if len(str(provenance.get(field) or "")) != _HASH_LEN:
                errors.append(f"PROVENANCE_{field.upper()}_INVALID")
        raw_hashes = provenance.get("raw_source_hashes")
        if (
            not isinstance(raw_hashes, list)
            or not raw_hashes
            or any(len(str(value)) != _HASH_LEN for value in raw_hashes)
        ):
            errors.append("PROVENANCE_RAW_SOURCE_HASHES_INVALID")

    limits = record.get("policy_limits") or {}
    latency = record.get("latency") or {}
    for field in (
        "source_to_receive_ms",
        "decision_to_arrival_ms",
        "book_age_ms",
        "cross_source_skew_ms",
    ):
        if latency.get(field) is None:
            errors.append(f"LATENCY_{field.upper()}_MISSING")
        else:
            try:
                if int(latency[field]) < 0:
                    errors.append(f"LATENCY_{field.upper()}_NEGATIVE")
            except (TypeError, ValueError):
                errors.append(f"LATENCY_{field.upper()}_INVALID")

    max_age = limits.get("max_book_age_ms")
    max_skew = limits.get("max_cross_source_skew_ms")
    if max_age is None:
        errors.append("MAX_BOOK_AGE_POLICY_MISSING")
    else:
        try:
            if int(max_age) < 0:
                errors.append("MAX_BOOK_AGE_POLICY_INVALID")
        except (TypeError, ValueError):
            errors.append("MAX_BOOK_AGE_POLICY_INVALID")
    if max_skew is None:
        errors.append("MAX_CROSS_SOURCE_SKEW_POLICY_MISSING")
    else:
        try:
            if int(max_skew) < 0:
                errors.append("MAX_CROSS_SOURCE_SKEW_INVALID")
        except (TypeError, ValueError):
            errors.append("MAX_CROSS_SOURCE_SKEW_INVALID")

    fixed: set[int] = set()
    try:
        fixed = {int(v) for v in limits.get("fixed_markout_horizons_s", [])}
    except (TypeError, ValueError):
        errors.append("MARKOUT_HORIZON_INVALID")
    for markout in record.get("markouts") or []:
        try:
            horizon = int(markout.get("horizon_s"))
        except (TypeError, ValueError, AttributeError):
            errors.append("MARKOUT_HORIZON_INVALID")
            continue
        if markout.get("predeclared") is not True or horizon not in fixed:
            errors.append("MARKOUT_NOT_PREDECLARED")

    copyability = record.get("source_copyability")
    if copyability is not None:
        if not isinstance(copyability, dict):
            errors.append("SOURCE_COPYABILITY_INVALID")
        elif not copyability.get("classification") or len(
            str(copyability.get("evidence_hash") or "")
        ) != _HASH_LEN:
            errors.append("SOURCE_COPYABILITY_EVIDENCE_INVALID")

    return list(dict.fromkeys(errors))


def canonical_record_hash(record: dict[str, Any]) -> str:
    return _sha256(record)

_FORBIDDEN_DECISION_KEY_PARTS = (
    "arrival",
    "markout",
    "resolution",
    "settled",
    "settlement",
    "winner",
    "realized",
    "post_outcome",
    "execution_false_label",
)


def _walk_decision_keys(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key).lower()
            yield from _walk_decision_keys(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_decision_keys(child, f"{prefix}[{index}]")


def assert_decision_packet_no_leakage(payload: dict[str, Any]) -> None:
    offenders = [
        path
        for path, key in _walk_decision_keys(payload)
        if any(part in key for part in _FORBIDDEN_DECISION_KEY_PARTS)
    ]
    if offenders:
        raise ValueError("decision_packet_leakage:" + ",".join(sorted(offenders)))


def _spread(book: dict[str, Any]) -> str | None:
    try:
        best_bid = max(Decimal(str(level[0])) for level in book["bids"])
        best_ask = min(Decimal(str(level[0])) for level in book["asks"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return str(best_ask - best_bid)


def build_jev_input_at_decision(record: dict[str, Any]) -> dict[str, Any]:
    """Pinned decision-half of ARQ3 build_jev_handoff_packet at ARQ3_JEV_PACKET_SOURCE_SHA."""
    decision_book = record["decision_book"]
    decision_execution = record.get("decision_execution") or {}
    copyability = record.get("source_copyability") or {}
    decision = {
        "schema_version": "jev_execution_handoff_v1",
        "opportunity_id": record["opportunity_id"],
        "market_slug": record["market_slug"],
        "condition_id": record["condition_id"],
        "token_id": record["token_id"],
        "decision_time": record["decision_time"],
        "book_age_ms": record["latency"].get("book_age_ms"),
        "book_skew_ms": record["latency"].get("cross_source_skew_ms"),
        "spread": _spread(decision_book),
        "depth_at_requested_size": decision_execution.get("executable_shares"),
        "decision_vwap": decision_execution.get("vwap"),
        "fee_status": decision_execution.get("fee_status"),
        "source_copyability_classification": copyability.get("classification"),
        "market_metadata": {
            "source": decision_book.get("source"),
            "tick_size": decision_book.get("tick_size"),
            "minimum_order_size": decision_book.get("minimum_order_size"),
        },
        "candidate": {
            "side": (record.get("candidate") or {}).get("side"),
            "outcome": (record.get("candidate") or {}).get("outcome"),
            "requested_notional_usd": (record.get("candidate") or {}).get(
                "requested_notional_usd"
            ),
            "requested_shares": (record.get("candidate") or {}).get(
                "requested_shares"
            ),
        },
    }
    assert_decision_packet_no_leakage(decision)
    return decision

