from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from senecio_polymarket.backend.gptrader.paper_book import (
    FIXED_PRIMARY_RISK_PCT,
    GPTraderPaperBook,
)
from senecio_polymarket.backend.gptrader.store import GPTraderStore


def run(coro):
    return asyncio.run(coro)


def packet(
    *,
    packet_id: str = "gpt0-p2",
    direction: str = "LONG",
    confidence: float = 0.80,
    price: float = 100.0,
) -> dict:
    return {
        "packet_id": packet_id,
        "timestamp": "2026-09-26T20:00:00+00:00",
        "symbol": "BTCUSDT",
        "prediction": direction,
        "confidence": confidence,
        "ev": 0.01,
        "price_now": price,
    }


def decision(packet_id: str, action: str = "TAKE", **extra) -> dict:
    return {
        "policy_id": "GPTRADER_TEST",
        "packet_id": packet_id,
        "action": action,
        "reason_codes": ["TEST"],
        **extra,
    }


def test_take_uses_namespaced_fixed_risk_paper_book(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    book = GPTraderPaperBook(store)
    result = run(book.apply_decision(packet(), decision("gpt0-p2"), run_id="run-p2"))
    state = book.state()
    assert result["classification"] == "TAKE_ACCEPTED"
    assert result["primary_risk_pct"] == FIXED_PRIMARY_RISK_PCT == 0.005
    assert result["primary_size_scale"] == 1.0
    assert result["filled_qty"] > 0
    assert state["paper_only"] is True
    assert state["simulation_only"] is True
    assert state["live"] is False
    assert state["orders_enabled"] is False
    assert state["journal_path"] == str(tmp_path / "trades.jsonl")
    assert state["open_count"] == 1


def test_abstain_records_no_fill(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    book = GPTraderPaperBook(store)
    result = run(book.apply_decision(
        packet(packet_id="gpt0-abstain"),
        decision("gpt0-abstain", action="ABSTAIN"),
        run_id="run-abstain",
    ))
    assert result["classification"] == "ABSTAIN"
    assert book.state()["open_count"] == 0
    assert len(store.read_decisions()) == 1


def test_risk_kernel_reject_is_not_abstain(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    book = GPTraderPaperBook(store)
    result = run(book.apply_decision(
        packet(packet_id="gpt0-low", confidence=0.10),
        decision("gpt0-low"),
        run_id="run-low",
    ))
    assert result["classification"] == "TAKE_REJECTED_BY_KERNEL"
    assert book.state()["open_count"] == 0


def test_no_flip_or_direction_override_and_size_scale_is_secondary(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    book = GPTraderPaperBook(store)
    with pytest.raises(ValueError, match="action"):
        run(book.apply_decision(packet(), decision("gpt0-p2", action="FLIP"), run_id="bad"))
    with pytest.raises(ValueError, match="direction"):
        run(book.apply_decision(
            packet(),
            decision("gpt0-p2", direction_override="SHORT"),
            run_id="bad-direction",
        ))
    scaled = run(book.apply_decision(
        packet(packet_id="gpt0-scale"),
        decision("gpt0-scale", size_scale=9.0),
        run_id="run-scale",
    ))
    assert scaled["primary_size_scale"] == 1.0
    assert scaled["exploratory_size_scale"] == 9.0


def test_restart_recovers_namespaced_open_state(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    first = GPTraderPaperBook(store)
    run(first.apply_decision(
        packet(packet_id="gpt0-restart"),
        decision("gpt0-restart"),
        run_id="run-restart",
    ))
    before = first.state()

    restarted = GPTraderPaperBook(GPTraderStore(tmp_path))
    after = restarted.state()
    assert after["open_count"] == before["open_count"] == 1
    assert after["cash"] == pytest.approx(before["cash"])
    assert after["risk_state"]["proposals_evaluated"] == 1


def test_same_input_replay_is_deterministic_across_fresh_books(tmp_path: Path) -> None:
    left = GPTraderPaperBook(GPTraderStore(tmp_path / "left"))
    right = GPTraderPaperBook(GPTraderStore(tmp_path / "right"))
    pkt = packet(packet_id="gpt0-deterministic")
    dec = decision(pkt["packet_id"])

    a = run(left.apply_decision(pkt, dec, run_id="run-a"))
    b = run(right.apply_decision(pkt, dec, run_id="run-b"))
    for key in (
        "classification",
        "ordered_qty",
        "filled_qty",
        "avg_fill_price",
        "primary_size_scale",
    ):
        assert a[key] == b[key]


def test_owner_scale_public_state_never_exposes_private_starting_cash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENEX_GPTRADER_OWNER_SCALE_STARTING_EQUITY", "123.45")
    book = GPTraderPaperBook(GPTraderStore(tmp_path), owner_scale=True)
    public = book.state(public=True)
    encoded = json.dumps(public, sort_keys=True)
    assert public["scale_label"] == "OWNER_SCALE_PRIVATE"
    assert "starting_equity" not in encoded
    assert "123.45" not in encoded
    assert "cash" not in public
    assert "equity" not in public
    assert "normalized_cash_pct" in public


def test_gptrader_p2_has_no_direct_d1_or_live_capability_imports() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "senecio_polymarket"
        / "backend"
        / "gptrader"
    )
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
    ).lower()
    for forbidden in (
        "supabase_client",
        "cloudflare",
        "create_market_order",
        "fetch_balance",
        "withdraw(",
    ):
        assert forbidden not in text
