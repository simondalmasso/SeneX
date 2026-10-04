from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "senecio_polymarket" / "backend" / "binance_sim_lane.py"


def _load():
    spec = importlib.util.spec_from_file_location("binance_sim_lane", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prediction(direction: str = "LONG", *, price: float = 100.0, ts: str = "2026-10-04T12:00:00Z"):
    return {
        "id": 9001,
        "ts": ts,
        "timestamp": ts,
        "symbol": "BTCUSDT",
        "prediction": direction,
        "confidence": 0.61,
        "price_now": price,
    }


def test_initial_wallet_is_isolated_and_exact(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")

    state = lane.public_state()

    assert state["lane_id"] == "BINANCE_SIM_18_63631644"
    assert state["simulation_only"] is True
    assert state["venue"] == "BINANCE_SIMULATED"
    assert state["starting_bankroll_usdt"] == pytest.approx(18.63631644)
    assert state["cash_usdt"] == pytest.approx(18.63631644)
    assert state["equity_usdt"] == pytest.approx(18.63631644)
    assert state["open_position"] is None
    assert state["total_simulated_orders"] == 0
    assert state["live_orders_possible"] is False
    assert state["research_verdict"] == "INCREMENTAL_EDGE_NOT_DEMONSTRATED"


def test_long_prediction_opens_one_quarter_equity_notional(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")

    event = lane.on_prediction(_prediction("LONG", price=100.0))

    assert event["action"] == "OPEN"
    state = lane.public_state()
    pos = state["open_position"]
    assert pos is not None
    assert pos["direction"] == "LONG"
    assert pos["reference_price"] == pytest.approx(100.0)
    assert pos["entry_price"] == pytest.approx(100.02)
    assert pos["notional_usdt"] == pytest.approx(18.63631644 * 0.25)
    assert pos["stop_price"] == pytest.approx(pos["entry_price"] * 0.98)
    assert pos["target_price"] == pytest.approx(pos["entry_price"] * 1.04)
    assert state["total_simulated_orders"] == 1
    assert state["fees_paid_usdt"] > 0
    assert state["cash_usdt"] < state["starting_bankroll_usdt"]


def test_open_position_blocks_second_entry(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")

    lane.on_prediction(_prediction("LONG", price=100.0))
    event = lane.on_prediction(
        _prediction("SHORT", price=99.0, ts="2026-10-04T12:15:00Z")
    )

    assert event["action"] == "HOLD"
    assert event["reason"] == "POSITION_ALREADY_OPEN"
    assert lane.public_state()["total_simulated_orders"] == 1


def test_target_exit_realizes_pnl_and_does_not_reenter_same_tick(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")
    lane.on_prediction(_prediction("LONG", price=100.0))

    state = lane.public_state()
    target = state["open_position"]["target_price"]
    event = lane.on_prediction(
        _prediction("LONG", price=target * 1.001, ts="2026-10-04T12:15:00Z")
    )

    assert event["action"] == "CLOSE"
    assert event["reason"] == "TARGET"
    closed = lane.public_state()
    assert closed["open_position"] is None
    assert closed["closed_trade_count"] == 1
    assert closed["realized_pnl_usdt"] > 0
    assert closed["cash_usdt"] > closed["starting_bankroll_usdt"]
    assert closed["total_simulated_orders"] == 1


def test_time_stop_closes_after_sixty_minutes(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")
    lane.on_prediction(_prediction("SHORT", price=100.0))

    event = lane.on_prediction(
        _prediction("SHORT", price=100.0, ts="2026-10-04T13:00:01Z")
    )

    assert event["action"] == "CLOSE"
    assert event["reason"] == "TIME_STOP"
    assert lane.public_state()["closed_trade_count"] == 1


def test_non_btc_or_flat_never_opens(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")

    eth = _prediction("LONG")
    eth["symbol"] = "ETHUSDT"
    assert lane.on_prediction(eth)["action"] == "IGNORE"
    assert lane.on_prediction(_prediction("FLAT"))["action"] == "IGNORE"
    assert lane.public_state()["open_position"] is None


def test_state_persists_without_credentials_or_network(tmp_path):
    m = _load()
    path = tmp_path / "lane.json"
    lane = m.BinanceSimLane(state_path=path)
    lane.on_prediction(_prediction("LONG", price=100.0))

    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(raw).lower()
    assert "secret" not in json.dumps(raw).lower()

    restored = m.BinanceSimLane(state_path=path)
    assert restored.public_state()["open_position"]["direction"] == "LONG"
    assert restored.public_state()["starting_bankroll_usdt"] == pytest.approx(18.63631644)


def test_flat_signal_still_closes_existing_position_on_time_stop(tmp_path):
    m = _load()
    lane = m.BinanceSimLane(state_path=tmp_path / "lane.json")
    lane.on_prediction(_prediction("LONG", price=100.0))

    event = lane.on_prediction(
        _prediction("FLAT", price=100.0, ts="2026-10-04T13:00:01Z")
    )

    assert event["action"] == "CLOSE"
    assert event["reason"] == "TIME_STOP"
    assert lane.public_state()["open_position"] is None


def test_module_has_no_live_exchange_or_secret_capability():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        "import ccxt",
        "from ccxt",
        "requests.",
        "httpx.",
        "create_order",
        "create_market_order",
        "place_market_order",
        "fetch_balance",
        "withdraw",
        "api_key",
        "secret_key",
        "private_key",
    ):
        assert forbidden not in source, forbidden
