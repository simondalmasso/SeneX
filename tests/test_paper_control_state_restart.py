from __future__ import annotations

import json
from pathlib import Path

import pytest

from senecio_polymarket.backend.portfolio.coordinator import PortfolioCoordinator
from senecio_polymarket.backend.portfolio.execution_engine import Position


def _config(tmp_path: Path, *, allow_bootstrap: bool = False) -> dict:
    return {
        "starting_equity_usd": 10_000.0,
        "starting_cash": 10_000.0,
        "journal_path": str(tmp_path / "trades.jsonl"),
        "paper_control_state_path": str(tmp_path / "paper_control_state.json"),
        "output_path": str(tmp_path / "shadow_trades.jsonl"),
        "report_path": str(tmp_path / "shadow_report.json"),
        "fetch_real_book": False,
        "paper_control_allow_journal_bootstrap": allow_bootstrap,
    }


def _closed_position(pid: str = "pos-closed") -> Position:
    return Position(
        position_id=pid,
        symbol="BTCUSDT",
        direction="LONG",
        qty=0.01,
        avg_entry_price=100.0,
        entry_ts="2026-10-04T01:00:00+00:00",
        stop_price=95.0,
        target_price=105.0,
        status="CLOSED",
        exit_price=99.0,
        exit_ts="2026-10-04T02:00:00+00:00",
        exit_reason="TIME_STOP",
        realized_pnl=-1.25,
        fees_paid=0.25,
        risk_usd=10.0,
        proposal_id=123,
    )


def _open_position(pid: str = "pos-open") -> Position:
    return Position(
        position_id=pid,
        symbol="BTCUSDT",
        direction="SHORT",
        qty=0.02,
        avg_entry_price=100.0,
        entry_ts="2026-10-05T01:00:00+00:00",
        stop_price=105.0,
        target_price=95.0,
        status="OPEN",
        fees_paid=0.10,
        risk_usd=10.0,
        proposal_id=456,
    )


def test_restart_restores_execution_risk_and_last_prices(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    first.execution_engine.cash = 9974.94
    first.execution_engine.closed_positions = [_closed_position()]
    first.execution_engine._order_count_offset = 7
    first.risk_kernel.state.current_equity = 9974.94
    first.risk_kernel.state.peak_equity = 10000.0
    first.risk_kernel.state.drawdown_pct = 0.2506
    first.risk_kernel.state.consecutive_losses = 3
    first.risk_kernel.state.proposals_evaluated = 11
    first._last_prices = {"BTCUSDT": 85582.8}
    first._persist_control_state()

    second = PortfolioCoordinator(config=cfg)
    second.start()

    assert second.execution_engine.cash == pytest.approx(9974.94)
    assert second.execution_engine.stats()["closed_positions"] == 1
    assert second.execution_engine.stats()["total_orders"] == 7
    assert second.risk_kernel.state.current_equity == pytest.approx(9974.94)
    assert second.risk_kernel.state.drawdown_pct == pytest.approx(0.2506)
    assert second.risk_kernel.state.consecutive_losses == 3
    assert second.risk_kernel.state.proposals_evaluated == 11
    assert second._last_prices == {"BTCUSDT": 85582.8}


def test_restart_restores_open_position_and_journal_pending(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    pos = _open_position()
    first.execution_engine.positions[pos.symbol] = pos
    first.execution_engine.cash = 12000.0
    first._persist_control_state()

    second = PortfolioCoordinator(config=cfg)
    second.start()

    restored = second.execution_engine.positions["BTCUSDT"]
    assert restored.position_id == "pos-open"
    assert restored.direction == "SHORT"
    assert "pos-open" in second.trade_journal._pending


def test_existing_journal_without_state_fails_closed(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    Path(cfg["journal_path"]).write_text(
        json.dumps({"trade_id": "legacy-1", "realized_pnl_usd": -25.06}) + "\n",
        encoding="utf-8",
    )

    coord = PortfolioCoordinator(config=cfg)
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_MISSING_WITH_DURABLE_JOURNAL"):
        coord.start()


def test_explicit_journal_bootstrap_is_conservative_and_persists(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_bootstrap=True)
    rows = [
        {
            "trade_id": "legacy-1",
            "prediction_id": 1,
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_ts": "2026-10-04T01:00:00+00:00",
            "exit_ts": "2026-10-04T02:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 99.0,
            "qty": 1.0,
            "realized_pnl_usd": -10.0,
            "total_fees_usd": 1.0,
            "risk_usd": 100.0,
            "exit_reason": "TIME_STOP",
        },
        {
            "trade_id": "legacy-2",
            "prediction_id": 2,
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "entry_ts": "2026-10-04T03:00:00+00:00",
            "exit_ts": "2026-10-04T04:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 99.0,
            "qty": 1.0,
            "realized_pnl_usd": 4.0,
            "total_fees_usd": 1.0,
            "risk_usd": 100.0,
            "exit_reason": "TIME_STOP",
        },
    ]
    Path(cfg["journal_path"]).write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    coord = PortfolioCoordinator(config=cfg)
    coord.start()

    assert coord.execution_engine.cash == pytest.approx(9994.0)
    assert coord.execution_engine.stats()["closed_positions"] == 2
    assert coord.execution_engine.stats()["total_orders"] == 2
    assert coord._control_state_migration == "JOURNAL_BOOTSTRAP_EXPLICIT"
    assert Path(cfg["paper_control_state_path"]).exists()

    # The bootstrap is one-time. A normal restart must restore the state file,
    # not replay the journal again.
    cfg2 = dict(cfg)
    cfg2["paper_control_allow_journal_bootstrap"] = False
    restarted = PortfolioCoordinator(config=cfg2)
    restarted.start()
    assert restarted.execution_engine.cash == pytest.approx(9994.0)
    assert restarted.execution_engine.stats()["closed_positions"] == 2


def test_corrupt_state_never_silently_resets(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    Path(cfg["paper_control_state_path"]).write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT"):
        PortfolioCoordinator(config=cfg).start()


def test_on_tick_checkpoints_completed_exit_for_restart(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.update({
        "exit_slippage_bps": 0.0,
        "taker_fee_bps": 0.0,
    })
    first = PortfolioCoordinator(config=cfg)
    first.start()
    pos = Position(
        position_id="pos-auto",
        symbol="BTCUSDT",
        direction="LONG",
        qty=1.0,
        avg_entry_price=100.0,
        entry_ts="2026-10-05T00:00:00+00:00",
        stop_price=95.0,
        target_price=105.0,
        status="OPEN",
        risk_usd=100.0,
        proposal_id=999,
    )
    first.execution_engine.positions[pos.symbol] = pos
    first.execution_engine.cash = 9900.0
    first.trade_journal.on_audit_event(
        {"event": "POSITION_OPEN", "position": pos.to_dict()}
    )

    exits = first.on_tick(
        "BTCUSDT",
        110.0,
        "2026-10-05T01:01:00+00:00",
    )
    assert len(exits) == 1
    expected_cash = first.execution_engine.cash

    restarted = PortfolioCoordinator(config=cfg)
    restarted.start()
    assert restarted.execution_engine.positions == {}
    assert restarted.execution_engine.stats()["closed_positions"] == 1
    assert restarted.execution_engine.cash == pytest.approx(expected_cash)
    assert restarted.risk_kernel.state.current_equity == pytest.approx(expected_cash)
