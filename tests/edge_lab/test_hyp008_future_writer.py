from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from edge_lab.arq3_execution_contract_v1 import (
    ARQ3_CONTRACT_SOURCE_SHA,
    MAX_CAPTURE_LATENESS_MS,
    validate_execution_record,
)
from edge_lab.hyp008_future_writer import (
    EXPECTED_PROTOCOL_HASH,
    FutureRowRejected,
    append_future_row,
    build_future_row,
    opportunity_id,
)

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "research" / "hyp008_prospective_protocol_v1.json"
OBS1_PATH = ROOT / "research" / "hyp008_prospective_cohort.jsonl"
OBS1_SHA256 = "a6e7384c7f94b0f4d5faa9d9754344e466b1f7e940184a1cea12fca8a7c0025b"
OBS1_ID = "HYP008-3b15a6ef53b4c1da3f9a861c"
PINNED_ARQ3_SHA = "92086c3d3227afe524fe86fa983cae1d291a1168"
START = "2026-09-21T01:00:00Z"
END = "2026-09-21T02:00:00Z"
CONDITION = "0xabc123"


def _epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _market() -> dict:
    return {
        "series_id": "10114",
        "event_id": "future-event-1",
        "market_id": "future-market-1",
        "conditionId": CONDITION,
        "slug": "bitcoin-up-or-down-september-20-2026-9pm-et",
        "eventStartTime": START,
        "endDate": END,
        "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
        "description": (
            "This market resolves Up if the Binance BTC/USDT 1 hour candle "
            "close is greater than or equal to its open; otherwise Down."
        ),
        "outcomes": "[\"Up\",\"Down\"]",
        "clobTokenIds": "[\"up-token\",\"down-token\"]",
    }


def _source() -> dict:
    return {
        "id": 6001,
        "ts": "2026-09-21T00:47:00+00:00",
        "created_at": "2026-09-21T00:47:01+00:00",
        "symbol": "BTCUSDT",
        "prediction": "SHORT",
        "confidence": 0.91,
        "ev": 0.001,
        "price_now": 80000.0,
        "exchange_used": "okx",
        "polymarket_directional_use": False,
        "polymarket_effective_weight": 0,
    }


def _arm() -> dict:
    return {
        "ready_before_boundary": True,
        "condition_id": CONDITION,
        "eventStartTime": START,
        "armed_at": "2026-09-21T00:59:30Z",
    }


def _lightweight_execution() -> dict:
    t = _epoch_ms(START)
    return {
        "schema_version": "execution_evidence_v1",
        "opportunity_id": opportunity_id(CONDITION, START),
        "event_id": "future-event-1",
        "condition_id": CONDITION,
        "market_slug": "bitcoin-up-or-down-september-20-2026-9pm-et",
        "event_time": t,
        "decision_time": t,
        "arrival_time": t + 100,
        "decision_book": {"bids": [["0.49", "10"]], "asks": [["0.51", "10"]]},
        "arrival_book": {"bids": [["0.49", "10"]], "asks": [["0.51", "10"]]},
        "execution": {"executable": True},
        "provenance": {
            "code_hash": "a" * 64,
            "config_hash": "b" * 64,
            "policy_hash": "c" * 64,
        },
    }


def _valid_execution() -> dict:
    t = _epoch_ms(START)
    source_time = t - 100
    receipt_time = t + 500
    decision_time = t + 500
    arrival_time = t + 700

    raw_decision = {
        "bids": [["0.49", "10"]],
        "asks": [["0.51", "10"]],
        "tick_size": "0.01",
        "min_order_size": "5",
    }
    raw_arrival = {
        "bids": [["0.48", "10"]],
        "asks": [["0.52", "10"]],
        "tick_size": "0.01",
        "min_order_size": "5",
    }
    decision_hash = _canonical_hash(raw_decision)
    arrival_hash = _canonical_hash(raw_arrival)

    decision_book = {
        "source": "POLYMARKET_CLOB_PUBLIC",
        "source_timestamp": source_time,
        "received_timestamp": receipt_time,
        "sha256": decision_hash,
        "bids": deepcopy(raw_decision["bids"]),
        "asks": deepcopy(raw_decision["asks"]),
        "tick_size": "0.01",
        "minimum_order_size": "5",
        "fee_schedule": {"status": "FEE_FREE"},
    }
    arrival_book = {
        "source": "POLYMARKET_CLOB_PUBLIC",
        "source_timestamp": t + 550,
        "received_timestamp": arrival_time,
        "sha256": arrival_hash,
        "bids": deepcopy(raw_arrival["bids"]),
        "asks": deepcopy(raw_arrival["asks"]),
        "tick_size": "0.01",
        "minimum_order_size": "5",
        "fee_schedule": {"status": "FEE_FREE"},
    }

    return {
        "schema_version": "execution_evidence_v1",
        "opportunity_id": opportunity_id(CONDITION, START),
        "event_id": "future-event-1",
        "condition_id": CONDITION,
        "token_id": "down-token",
        "market_slug": "bitcoin-up-or-down-september-20-2026-9pm-et",
        "event_time": t,
        "source_time": source_time,
        "receipt_time": receipt_time,
        "decision_time": decision_time,
        "arrival_time": arrival_time,
        "decision_book": decision_book,
        "arrival_book": arrival_book,
        "raw_evidence": {
            "decision_book": raw_decision,
            "arrival_book": raw_arrival,
        },
        "candidate": {
            "side": "BUY",
            "outcome": "DOWN",
            "requested_notional_usd": "2.55",
            "requested_shares": "5",
        },
        "execution": {
            "executable": True,
            "reject_reason": None,
            "executable_shares": "5",
            "fill_fraction": "1",
            "levels_consumed": 1,
            "best_price": "0.52",
            "vwap": "0.52",
            "spread_cost_usd": "0.025",
            "depth_slippage_usd": "0",
            "fees_usd": "0",
            "rebate_usd_if_proven": "0",
            "total_entry_cost_usd": "2.60",
        },
        "decision_execution": {
            "executable_shares": "5",
            "vwap": "0.51",
            "fee_status": "FEE_FREE",
        },
        "latency": {
            "source_to_receive_ms": receipt_time - source_time,
            "decision_to_arrival_ms": arrival_time - decision_time,
            "book_age_ms": decision_time - source_time,
            "cross_source_skew_ms": 50,
        },
        "markouts": [],
        "adverse_selection": {},
        "source_copyability": {
            "classification": "PUBLIC_READ_ONLY_CLOB",
            "evidence_hash": "d" * 64,
        },
        "provenance": {
            "code_hash": "a" * 64,
            "config_hash": "b" * 64,
            "policy_hash": "c" * 64,
            "raw_source_hashes": [decision_hash, arrival_hash],
        },
        "policy_limits": {
            "max_book_age_ms": 1000,
            "max_cross_source_skew_ms": 1000,
            "fixed_markout_horizons_s": [],
        },
    }


def _capture_timing(*, lateness_ms: int = 800) -> dict:
    t = _epoch_ms(START)
    completed = t + lateness_ms
    started = t + 8
    request = t + 8
    receipt = t + 500
    return {
        "scheduled_due_ms": t,
        "capture_started_ms": started,
        "request_timestamp": _iso_from_ms(request),
        "receipt_timestamp": _iso_from_ms(receipt),
        "capture_completed_ms": completed,
        "actual_capture_ms": completed,
        "lateness_ms": lateness_ms,
        "TIMING_VALID": lateness_ms <= MAX_CAPTURE_LATENESS_MS,
    }


def _expected_jev_input(record: dict) -> dict:
    decision_book = record["decision_book"]
    decision_execution = record.get("decision_execution") or {}
    copyability = record.get("source_copyability") or {}
    best_bid = max(Decimal(str(level[0])) for level in decision_book["bids"])
    best_ask = min(Decimal(str(level[0])) for level in decision_book["asks"])
    return {
        "schema_version": "jev_execution_handoff_v1",
        "opportunity_id": record["opportunity_id"],
        "market_slug": record["market_slug"],
        "condition_id": record["condition_id"],
        "token_id": record["token_id"],
        "decision_time": record["decision_time"],
        "book_age_ms": record["latency"].get("book_age_ms"),
        "book_skew_ms": record["latency"].get("cross_source_skew_ms"),
        "spread": str(best_ask - best_bid),
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
            "requested_notional_usd": (record.get("candidate") or {}).get("requested_notional_usd"),
            "requested_shares": (record.get("candidate") or {}).get("requested_shares"),
        },
    }


def _jev_input(execution: dict | None = None) -> dict:
    return _expected_jev_input(execution or _valid_execution())


def _kwargs() -> dict:
    execution = _valid_execution()
    return {
        "protocol_path": PROTOCOL_PATH,
        "market": _market(),
        "source_senex": _source(),
        "scheduled_boundary_timestamp": START,
        "arq3_arm": _arm(),
        "arq3_capture_timing": _capture_timing(),
        "execution_evidence": execution,
        "jev_input_at_decision": _expected_jev_input(execution),
    }


def test_pinned_contract_source_is_exact() -> None:
    assert ARQ3_CONTRACT_SOURCE_SHA == PINNED_ARQ3_SHA
    assert MAX_CAPTURE_LATENESS_MS == 2000


def test_prior_lightweight_execution_fixture_is_rejected() -> None:
    errors = validate_execution_record(_lightweight_execution())
    assert errors
    args = _kwargs()
    args["execution_evidence"] = _lightweight_execution()
    with pytest.raises(FutureRowRejected, match="EXECUTION_EVIDENCE_INVALID"):
        build_future_row(**args)


def test_boundary_information_source_is_preboundary_while_network_timing_is_truthfully_late() -> None:
    execution = _valid_execution()
    row = build_future_row(**_kwargs())
    boundary = _epoch_ms(START)
    assert execution["source_time"] < boundary
    assert execution["decision_book"]["source_timestamp"] < boundary
    assert execution["receipt_time"] > boundary
    assert execution["decision_time"] > boundary
    assert row["timing_truth"]["capture_started_ms"] > boundary
    assert row["timing_truth"]["capture_completed_ms"] > boundary
    assert row["timing_truth"]["capture_completed_ms"] <= boundary + 2000
    assert row["timing_truth"]["lateness_ms"] == 800
    assert row["source_senex"]["ts"] <= START
    assert row["source_senex"]["created_at"] <= START


def test_exact_arq3_derived_jev_packet_is_accepted() -> None:
    execution = _valid_execution()
    expected = _expected_jev_input(execution)
    args = _kwargs()
    args["jev_input_at_decision"] = deepcopy(expected)
    row = build_future_row(**args)
    assert row["JEV_INPUT_AT_DECISION"] == expected


def test_source_senex_created_at_after_boundary_fails_closed() -> None:
    args = _kwargs()
    args["source_senex"]["created_at"] = "2026-09-21T01:00:00.001+00:00"
    with pytest.raises(FutureRowRejected, match="SENEX_CREATED_AT_AFTER_BOUNDARY"):
        build_future_row(**args)


def test_execution_source_time_after_boundary_fails_closed() -> None:
    args = _kwargs()
    boundary = _epoch_ms(START)
    execution = args["execution_evidence"]
    execution["source_time"] = boundary + 1
    execution["latency"]["source_to_receive_ms"] = execution["receipt_time"] - execution["source_time"]
    args["jev_input_at_decision"] = _expected_jev_input(execution)
    assert validate_execution_record(execution) == []
    with pytest.raises(FutureRowRejected, match="EXECUTION_SOURCE_AFTER_BOUNDARY"):
        build_future_row(**args)


def test_decision_book_source_timestamp_after_boundary_fails_closed() -> None:
    args = _kwargs()
    boundary = _epoch_ms(START)
    execution = args["execution_evidence"]
    execution["decision_book"]["source_timestamp"] = boundary + 1
    execution["latency"]["book_age_ms"] = execution["decision_time"] - (boundary + 1)
    args["jev_input_at_decision"] = _expected_jev_input(execution)
    assert validate_execution_record(execution) == []
    with pytest.raises(FutureRowRejected, match="DECISION_BOOK_SOURCE_AFTER_BOUNDARY"):
        build_future_row(**args)


def test_execution_candidate_must_equal_frozen_senex_candidate() -> None:
    args = _kwargs()
    execution = args["execution_evidence"]
    execution["candidate"]["outcome"] = "UP"
    execution["token_id"] = "up-token"
    args["jev_input_at_decision"] = _expected_jev_input(execution)
    with pytest.raises(FutureRowRejected, match="EXECUTION_CANDIDATE_MISMATCH"):
        build_future_row(**args)


def test_directional_token_must_match_immutable_candidate_outcome() -> None:
    args = _kwargs()
    execution = args["execution_evidence"]
    execution["token_id"] = "up-token"
    args["jev_input_at_decision"] = _expected_jev_input(execution)
    with pytest.raises(FutureRowRejected, match="EXECUTION_TOKEN_OUTCOME_MISMATCH"):
        build_future_row(**args)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.__setitem__("token_id", "up-token"),
        lambda p: p.__setitem__("spread", "0.03"),
        lambda p: p.__setitem__("book_age_ms", p["book_age_ms"] + 1),
        lambda p: p.__setitem__("book_skew_ms", p["book_skew_ms"] + 1),
        lambda p: p.__setitem__("decision_vwap", "0.52"),
        lambda p: p.__setitem__("depth_at_requested_size", "6"),
        lambda p: p.__setitem__("fee_status", "PROVEN"),
        lambda p: p["market_metadata"].__setitem__("source", "OTHER"),
        lambda p: p["market_metadata"].__setitem__("tick_size", "0.02"),
        lambda p: p["market_metadata"].__setitem__("minimum_order_size", "6"),
        lambda p: p["candidate"].__setitem__("requested_shares", "6"),
        lambda p: p["candidate"].__setitem__("outcome", "UP"),
    ],
)
def test_supplied_jev_packet_must_match_pinned_derivation_exactly(mutator) -> None:
    args = _kwargs()
    packet = deepcopy(args["jev_input_at_decision"])
    mutator(packet)
    args["jev_input_at_decision"] = packet
    with pytest.raises(FutureRowRejected, match="JEV_INPUT_EXACT_MISMATCH"):
        build_future_row(**args)


@pytest.mark.parametrize(
    "mutator,expected_error",
    [
        (lambda r: r.pop("token_id"), "TOKEN_ID_MISSING"),
        (lambda r: r.pop("source_time"), "SOURCE_TIME_MISSING"),
        (lambda r: r.__setitem__("source_time", r["receipt_time"] + 1), "SOURCE_TIME_AFTER_RECEIPT"),
        (lambda r: r.__setitem__("receipt_time", r["decision_time"] + 1), "RECEIPT_AFTER_DECISION"),
        (lambda r: r["decision_book"].pop("source"), "DECISION_BOOK_SOURCE_MISSING"),
        (lambda r: r["decision_book"].pop("source_timestamp"), "DECISION_BOOK_SOURCE_TIMESTAMP_MISSING"),
        (lambda r: r["decision_book"].pop("received_timestamp"), "DECISION_BOOK_RECEIVED_TIMESTAMP_MISSING"),
        (lambda r: r["decision_book"].pop("sha256"), "DECISION_BOOK_SHA256_MISSING"),
        (lambda r: r["decision_book"].pop("tick_size"), "DECISION_BOOK_TICK_SIZE_MISSING"),
        (lambda r: r["decision_book"].pop("minimum_order_size"), "DECISION_BOOK_MINIMUM_ORDER_SIZE_MISSING"),
        (lambda r: r["decision_book"].__setitem__("fee_schedule", {"status": "UNKNOWN"}), "DECISION_FEE_SCHEDULE_UNPROVEN"),
        (lambda r: r["decision_book"].__setitem__("source_timestamp", r["decision_time"] + 1), "DECISION_BOOK_FROM_FUTURE"),
        (lambda r: r["decision_book"].__setitem__("received_timestamp", r["decision_time"] + 1), "DECISION_BOOK_RECEIVED_AFTER_DECISION_TIME"),
        (lambda r: r["execution"].pop("fees_usd"), "EXECUTION_FEES_USD_MISSING"),
        (lambda r: (r["execution"].__setitem__("executable", False), r["execution"].__setitem__("reject_reason", None)), "REJECTION_REASON_MISSING"),
        (lambda r: r.pop("raw_evidence"), "RAW_EVIDENCE_MISSING"),
        (lambda r: r["raw_evidence"]["decision_book"].__setitem__("bids", [["0.01", "1"]]), "DECISION_RAW_HASH_MISMATCH"),
        (lambda r: r["provenance"].pop("raw_source_hashes"), "PROVENANCE_RAW_SOURCE_HASHES_INVALID"),
        (lambda r: r["latency"].__setitem__("book_age_ms", -1), "LATENCY_BOOK_AGE_MS_NEGATIVE"),
        (lambda r: r.pop("policy_limits"), "MAX_BOOK_AGE_POLICY_MISSING"),
        (lambda r: r.__setitem__("source_copyability", {"classification": "PUBLIC"}), "SOURCE_COPYABILITY_EVIDENCE_INVALID"),
    ],
)
def test_representative_arq3_invalid_records_fail_closed(mutator, expected_error: str) -> None:
    execution = _valid_execution()
    mutator(execution)
    assert expected_error in validate_execution_record(execution)
    args = _kwargs()
    args["execution_evidence"] = execution
    with pytest.raises(FutureRowRejected, match="EXECUTION_EVIDENCE_INVALID"):
        build_future_row(**args)


def test_capture_timing_later_than_authoritative_two_seconds_fails_closed() -> None:
    args = _kwargs()
    args["arq3_capture_timing"] = _capture_timing(lateness_ms=MAX_CAPTURE_LATENESS_MS + 1)
    with pytest.raises(FutureRowRejected, match="ARQ3_CAPTURE_TIMING_INVALID"):
        build_future_row(**args)


def test_capture_timing_requires_truthful_request_receipt_and_completion() -> None:
    args = _kwargs()
    args["arq3_capture_timing"]["request_timestamp"] = None
    with pytest.raises(FutureRowRejected, match="ARQ3_CAPTURE_TIMING_INVALID"):
        build_future_row(**args)


def test_protocol_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    doc = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    doc["protocol_hash"] = "0" * 64
    wrong = tmp_path / "protocol.json"
    wrong.write_text(json.dumps(doc), encoding="utf-8")
    args = _kwargs()
    args["protocol_path"] = wrong
    with pytest.raises(FutureRowRejected, match="PROTOCOL_HASH_MISMATCH"):
        build_future_row(**args)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.__setitem__("series_id", "999"),
        lambda m: m.__setitem__("outcomes", "[\"Yes\",\"No\"]"),
        lambda m: m.__setitem__("resolutionSource", "https://example.com/BTC_USDT"),
        lambda m: m.__setitem__("eventStartTime", "2026-09-21T01:00:01Z"),
        lambda m: m.__setitem__("endDate", "2026-09-21T03:00:00Z"),
    ],
)
def test_wrong_series_or_market_semantics_fail_closed(mutation) -> None:
    args = _kwargs()
    mutation(args["market"])
    with pytest.raises(FutureRowRejected):
        build_future_row(**args)


def test_source_senex_after_boundary_fails_closed() -> None:
    args = _kwargs()
    args["source_senex"]["ts"] = "2026-09-21T01:00:00.001+00:00"
    with pytest.raises(FutureRowRejected, match="SENEX_SOURCE_AFTER_BOUNDARY"):
        build_future_row(**args)


@pytest.mark.parametrize(
    "arm_update,reason",
    [
        ({"ready_before_boundary": False}, "ARQ3_NOT_ARMED"),
        ({"armed_at": START}, "ARQ3_ARM_NOT_PREBOUNDARY"),
    ],
)
def test_unarmed_boundary_fails_closed(arm_update: dict, reason: str) -> None:
    args = _kwargs()
    args["arq3_arm"].update(arm_update)
    with pytest.raises(FutureRowRejected, match=reason):
        build_future_row(**args)


def test_arq3_identity_mismatch_fails_closed() -> None:
    args = _kwargs()
    args["execution_evidence"]["condition_id"] = "0xwrong"
    with pytest.raises(FutureRowRejected, match="ARQ3_IDENTITY_MISMATCH"):
        build_future_row(**args)


@pytest.mark.parametrize("execution", [None, {}, {"schema_version": "wrong"}])
def test_missing_or_invalid_execution_evidence_fails_closed(execution) -> None:
    args = _kwargs()
    args["execution_evidence"] = execution
    with pytest.raises(FutureRowRejected, match="EXECUTION_EVIDENCE_INVALID"):
        build_future_row(**args)


def test_leaky_jev_decision_packet_fails_closed() -> None:
    args = _kwargs()
    args["jev_input_at_decision"]["arrival_vwap"] = "0.52"
    with pytest.raises(FutureRowRejected, match="JEV_INPUT_LEAKAGE"):
        build_future_row(**args)


def test_candidate_mapping_is_frozen_and_polymarket_direction_is_disabled() -> None:
    for prediction, expected in (("LONG", "UP"), ("SHORT", "DOWN"), ("FLAT", "ABSTAIN")):
        args = _kwargs()
        args["source_senex"]["prediction"] = prediction
        args["execution_evidence"]["candidate"]["outcome"] = expected
        if expected == "UP":
            args["execution_evidence"]["token_id"] = "up-token"
        args["jev_input_at_decision"] = _expected_jev_input(args["execution_evidence"])
        row = build_future_row(**args)
        assert row["candidate"] == expected
        assert row["source_senex"]["prediction"] == prediction
        assert row["source_senex"]["polymarket_directional_use"] is False
        assert row["source_senex"]["polymarket_effective_weight"] == 0
        assert row["protocol_hash"] == EXPECTED_PROTOCOL_HASH
        assert row["eligibility"] is True
        assert row["protocol_admissible"] is True


def test_obs1_opportunity_id_is_compatible_from_identity_fields_only() -> None:
    assert (
        opportunity_id(
            "0x72b6fbe592ecee523fa50be552fc6783ec25a79718fc56c3715e475c56af4ed7",
            "2026-09-20T05:00:00Z",
        )
        == OBS1_ID
    )


def test_duplicate_append_does_not_create_second_row(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.jsonl"
    first = append_future_row(cohort_path=cohort, **_kwargs())
    second = append_future_row(cohort_path=cohort, **_kwargs())
    assert first["status"] == "APPENDED"
    assert second["status"] == "DUPLICATE"
    assert len(cohort.read_text(encoding="utf-8").splitlines()) == 1


def test_observation_1_remains_byte_exact() -> None:
    payload = OBS1_PATH.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == OBS1_SHA256


def test_writer_has_no_network_order_live_or_capital_path() -> None:
    paths = [
        ROOT / "edge_lab" / "hyp008_future_writer.py",
        ROOT / "edge_lab" / "arq3_execution_contract_v1.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        assert imported.isdisjoint({"httpx", "requests", "urllib", "socket", "subprocess", "ccxt"})
        source = path.read_text(encoding="utf-8").lower()
        for forbidden in (
            "place_order",
            "create_order",
            "send_order",
            "wallet",
            "private_key",
            "live=true",
            "capital_unlock",
        ):
            assert forbidden not in source
