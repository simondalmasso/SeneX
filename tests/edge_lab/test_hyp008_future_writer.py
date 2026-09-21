from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

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
START = "2026-09-21T01:00:00Z"
END = "2026-09-21T02:00:00Z"
CONDITION = "0xabc123"


def _boundary_ms() -> int:
    return 179,  # replaced below


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


def _epoch_ms(value: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _arm() -> dict:
    return {
        "ready_before_boundary": True,
        "condition_id": CONDITION,
        "eventStartTime": START,
        "armed_at": "2026-09-21T00:59:30Z",
    }


def _execution() -> dict:
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
        "provenance": {"code_hash": "a" * 64, "config_hash": "b" * 64, "policy_hash": "c" * 64},
    }


def _jev_input() -> dict:
    t = _epoch_ms(START)
    return {
        "schema_version": "jev_execution_handoff_v1",
        "opportunity_id": opportunity_id(CONDITION, START),
        "market_slug": "bitcoin-up-or-down-september-20-2026-9pm-et",
        "condition_id": CONDITION,
        "token_id": "down-token",
        "decision_time": t,
        "book_age_ms": 0,
        "book_skew_ms": 0,
        "spread": "0.02",
        "depth_at_requested_size": "5",
        "decision_vwap": "0.51",
        "fee_status": "PROVEN",
        "source_copyability_classification": "PUBLIC_READ_ONLY_CLOB",
        "market_metadata": {"source": "POLYMARKET_CLOB_PUBLIC", "tick_size": "0.01", "minimum_order_size": "5"},
        "candidate": {"side": "BUY", "outcome": "DOWN", "requested_notional_usd": "2.55", "requested_shares": "5"},
    }


def _kwargs() -> dict:
    return {
        "protocol_path": PROTOCOL_PATH,
        "market": _market(),
        "source_senex": _source(),
        "decision_capture_timestamp": START,
        "arq3_arm": _arm(),
        "execution_evidence": _execution(),
        "jev_input_at_decision": _jev_input(),
    }


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
    "capture,arm_update,reason",
    [
        ("2026-09-21T01:00:00.001Z", {}, "BOUNDARY_CAPTURE_NOT_EXACT"),
        (START, {"ready_before_boundary": False}, "ARQ3_NOT_ARMED"),
        (START, {"armed_at": START}, "ARQ3_ARM_NOT_PREBOUNDARY"),
    ],
)
def test_late_or_unarmed_boundary_fails_closed(capture: str, arm_update: dict, reason: str) -> None:
    args = _kwargs()
    args["decision_capture_timestamp"] = capture
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
        args["jev_input_at_decision"]["candidate"]["outcome"] = expected
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
    writer = ROOT / "edge_lab" / "hyp008_future_writer.py"
    tree = ast.parse(writer.read_text(encoding="utf-8"))
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
    source = writer.read_text(encoding="utf-8").lower()
    for forbidden in ("place_order", "create_order", "send_order", "wallet", "private_key", "live=true", "capital_unlock"):
        assert forbidden not in source
