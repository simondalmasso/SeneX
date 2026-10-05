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


def _journal_row(
    trade_id: str,
    *,
    direction: str = "LONG",
    entry_ts: str = "2026-10-04T01:00:00+00:00",
    exit_ts: str = "2026-10-04T02:00:00+00:00",
    entry_price: float = 100.0,
    exit_price: float = 99.0,
    qty: float = 1.0,
    pnl: float = -1.0,
    total_fees: float = 1.0,
    risk_usd: float = 100.0,
    prediction_id: int = 1,
) -> dict:
    return {
        "journal_id": f"jr-{trade_id}",
        "trade_id": trade_id,
        "prediction_id": prediction_id,
        "symbol": "BTCUSDT",
        "direction": direction,
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "realized_pnl_usd": pnl,
        "risk_usd": risk_usd,
        "entry_fee_usd": total_fees / 2.0,
        "exit_fee_usd": total_fees / 2.0,
        "total_fees_usd": total_fees,
        "exit_reason": "TIME_STOP",
    }


def test_restart_restores_execution_risk_and_last_prices(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    first.execution_engine.cash = 9974.94
    first.execution_engine.closed_positions = [_closed_position()]
    Path(cfg["journal_path"]).write_text(
        json.dumps(
            _journal_row(
                "pos-closed",
                pnl=-1.25,
                total_fees=0.25,
                risk_usd=10.0,
                prediction_id=123,
            )
        )
        + "\n",
        encoding="utf-8",
    )
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
        _journal_row(
            "legacy-1",
            direction="LONG",
            entry_ts="2026-10-04T01:00:00+00:00",
            exit_ts="2026-10-04T02:00:00+00:00",
            pnl=-10.0,
            prediction_id=1,
        ),
        _journal_row(
            "legacy-2",
            direction="SHORT",
            entry_ts="2026-10-04T03:00:00+00:00",
            exit_ts="2026-10-04T04:00:00+00:00",
            pnl=4.0,
            prediction_id=2,
        ),
    ]
    Path(cfg["journal_path"]).write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    coord = PortfolioCoordinator(config=cfg)
    coord.start()

    assert coord.execution_engine.cash == pytest.approx(9994.0)
    assert coord.execution_engine.stats()["closed_positions"] == 2
    assert coord.execution_engine.closed_positions == []
    assert coord.execution_engine._closed_position_count_offset == 2
    assert coord.execution_engine._historical_closed_trade_ids == [
        "legacy-1",
        "legacy-2",
    ]
    assert coord.execution_engine.stats()["total_orders"] == 2
    assert coord._control_state_migration == "JOURNAL_BOOTSTRAP_EXPLICIT"
    assert coord._control_order_count_semantics == "MINIMUM_CLOSED_TRADES_AT_BOOTSTRAP"
    assert coord._decision_state_migration == {
        "meta_labeler": "RECONSTRUCTED_FROM_JOURNAL",
        "microstructure": "RESET_AT_LEGACY_BOOTSTRAP",
    }
    assert Path(cfg["paper_control_state_path"]).exists()

    # The bootstrap is one-time. A normal restart must restore the state file,
    # not replay the journal again.
    cfg2 = dict(cfg)
    cfg2["paper_control_allow_journal_bootstrap"] = False
    restarted = PortfolioCoordinator(config=cfg2)
    restarted.start()
    assert restarted.execution_engine.cash == pytest.approx(9994.0)
    assert restarted.execution_engine.stats()["closed_positions"] == 2
    assert restarted._control_order_count_semantics == "MINIMUM_CLOSED_TRADES_AT_BOOTSTRAP"


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


def test_failed_coordinator_start_never_publishes_partial_singleton(monkeypatch) -> None:
    from senecio_polymarket.backend import oracle_runner, paper_view
    from senecio_polymarket.backend import portfolio as portfolio_module

    created = []

    class FailingCoordinator:
        def __init__(self):
            created.append(self)

        def start(self):
            raise RuntimeError("restore failed")

    monkeypatch.setattr(portfolio_module, "PortfolioCoordinator", FailingCoordinator)
    monkeypatch.setattr(oracle_runner, "_portfolio_coordinator", None)
    monkeypatch.setattr(paper_view, "_model_quality_view", lambda: {"status": "TEST"})
    monkeypatch.setattr(paper_view, "_edge_view", lambda: {"status": "UNPROVEN"})

    assert oracle_runner._get_portfolio_coordinator() is None
    assert oracle_runner._portfolio_coordinator is None
    assert oracle_runner._get_portfolio_coordinator() is None
    assert oracle_runner._portfolio_coordinator is None
    assert len(created) == 2

    state = paper_view.paper_state(last_prices={})
    assert state["status"] == "NO_PORTFOLIO_PIPELINE"
    assert state["execution"]["status"] == "UNKNOWN"














def test_crash_after_journal_fsync_fails_closed_on_restart(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    pos = Position(
        position_id="pos-crash",
        symbol="BTCUSDT",
        direction="LONG",
        qty=1.0,
        avg_entry_price=100.0,
        entry_ts="2026-10-05T00:00:00+00:00",
        stop_price=95.0,
        target_price=105.0,
        status="OPEN",
        risk_usd=100.0,
        proposal_id=1001,
    )
    first.execution_engine.positions[pos.symbol] = pos
    first.execution_engine.cash = 9900.0
    first.trade_journal.on_audit_event(
        {"event": "POSITION_OPEN", "position": pos.to_dict()}
    )
    first._persist_control_state()

    def _crash_after_journal(event: dict) -> None:
        first.trade_journal.on_audit_event(event)
        if event.get("event") == "POSITION_EXIT":
            raise SystemExit("SIMULATED_CRASH_AFTER_JOURNAL_FSYNC")

    first.execution_engine.set_audit_listener(_crash_after_journal)
    with pytest.raises(SystemExit, match="SIMULATED_CRASH_AFTER_JOURNAL_FSYNC"):
        first.execution_engine.check_exits(
            "BTCUSDT",
            110.0,
            "2026-10-05T01:01:00+00:00",
        )

    rows = first.trade_journal.fetch_all()
    assert [row["trade_id"] for row in rows] == ["pos-crash"]

    restarted = PortfolioCoordinator(config=cfg)
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_JOURNAL_MISMATCH"):
        restarted.start()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_last_price_rejected_before_save(tmp_path: Path, bad: float) -> None:
    cfg = _config(tmp_path)
    coord = PortfolioCoordinator(config=cfg)
    coord.start()
    coord._last_prices = {"BTCUSDT": bad}
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT"):
        coord._persist_control_state()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_risk_value_rejected_before_save(tmp_path: Path, bad: float) -> None:
    cfg = _config(tmp_path)
    coord = PortfolioCoordinator(config=cfg)
    coord.start()
    coord.risk_kernel.state.current_equity = bad
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT"):
        coord._persist_control_state()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_position_value_rejected_before_save(tmp_path: Path, bad: float) -> None:
    cfg = _config(tmp_path)
    coord = PortfolioCoordinator(config=cfg)
    coord.start()
    pos = _open_position("pos-bad")
    pos.qty = bad
    coord.execution_engine.positions[pos.symbol] = pos
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT"):
        coord._persist_control_state()


def test_noninteger_counters_rejected_on_restore(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    state_path = Path(cfg["paper_control_state_path"])

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["execution"]["total_orders"] = 1.5
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT:TOTAL_ORDERS"):
        PortfolioCoordinator(config=cfg).start()

    payload["execution"]["total_orders"] = 0
    payload["risk_state"]["proposals_evaluated"] = 1.5
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT:PROPOSALS_EVALUATED"):
        PortfolioCoordinator(config=cfg).start()


def test_nonfinite_json_constant_rejected_on_restore(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    state_path = Path(cfg["paper_control_state_path"])
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["last_prices"] = {"BTCUSDT": float("nan")}
    state_path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")

    with pytest.raises(RuntimeError, match="PAPER_CONTROL_STATE_CORRUPT"):
        PortfolioCoordinator(config=cfg).start()


def test_meta_labeler_decision_state_survives_restart(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()
    for _ in range(3):
        first.meta_labeler.record_outcome("LONG", "LOSS")

    before_stats = first.meta_labeler.stats()
    before_label = first.meta_labeler.evaluate(
        direction="LONG",
        conviction=0.80,
        regime_4h="BULL",
        vol_pct=0.01,
        spread_bps=1.0,
        entry_price=100.0,
        stop_price=98.0,
        target_price=104.0,
        expected_ev_bps=20.0,
    )
    first._persist_control_state()

    second = PortfolioCoordinator(config=cfg)
    second.start()
    after_stats = second.meta_labeler.stats()
    after_label = second.meta_labeler.evaluate(
        direction="LONG",
        conviction=0.80,
        regime_4h="BULL",
        vol_pct=0.01,
        spread_bps=1.0,
        entry_price=100.0,
        stop_price=98.0,
        target_price=104.0,
        expected_ev_bps=20.0,
    )

    assert before_stats == after_stats
    assert before_label.take_trade == after_label.take_trade
    assert before_label.confidence_mult == after_label.confidence_mult
    assert before_label.confidence_mult == pytest.approx(0.55)


def test_microstructure_decision_state_survives_restart(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = PortfolioCoordinator(config=cfg)
    first.start()

    for bid, ask in (
        (100.0, 100.0),
        (140.0, 80.0),
        (170.0, 60.0),
        (210.0, 45.0),
        (250.0, 30.0),
    ):
        first.microstructure.ingest_top_of_book(bid, ask)
    first.microstructure.ingest_funding_oi(0.0007, 12.0)
    first.microstructure.liq.ingest_high_volume_node(100.0, 1_000_000.0)

    before_stats = first.microstructure.stats()
    first._persist_control_state()

    second = PortfolioCoordinator(config=cfg)
    second.start()
    after_stats = second.microstructure.stats()

    # Feed the exact same next observation to both instances. The decision
    # must be identical if the rolling information state survived restart.
    first.microstructure.ingest_top_of_book(280.0, 20.0)
    second.microstructure.ingest_top_of_book(280.0, 20.0)
    before_report = first.microstructure.evaluate(
        current_price=100.0,
        direction="LONG",
    )
    after_report = second.microstructure.evaluate(
        current_price=100.0,
        direction="LONG",
    )

    assert before_stats == after_stats
    assert before_report.toxic_score == after_report.toxic_score
    assert before_report.action == after_report.action
    assert before_report.size_scale == after_report.size_scale


def test_bootstrap_rejects_malformed_journal_line(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_bootstrap=True)
    Path(cfg["journal_path"]).write_text(
        json.dumps(_journal_row("ok")) + "\n" + "{truncated-json\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="PAPER_CONTROL_JOURNAL_CORRUPT"):
        PortfolioCoordinator(config=cfg).start()
    assert not Path(cfg["paper_control_state_path"]).exists()


def test_bootstrap_rejects_semantically_incomplete_journal_row(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_bootstrap=True)
    Path(cfg["journal_path"]).write_text(
        json.dumps({"realized_pnl_usd": -5.0}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="PAPER_CONTROL_JOURNAL_SCHEMA"):
        PortfolioCoordinator(config=cfg).start()
    assert not Path(cfg["paper_control_state_path"]).exists()


def test_bootstrap_rejects_exact_duplicate_trade_id(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_bootstrap=True)
    row = _journal_row("dup-exact", pnl=-2.0)
    Path(cfg["journal_path"]).write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="PAPER_CONTROL_JOURNAL_DUPLICATE"):
        PortfolioCoordinator(config=cfg).start()
    assert not Path(cfg["paper_control_state_path"]).exists()


def test_bootstrap_rejects_conflicting_duplicate_trade_id(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_bootstrap=True)
    first = _journal_row("dup-conflict", pnl=-2.0)
    second = _journal_row("dup-conflict", pnl=3.0)
    Path(cfg["journal_path"]).write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="PAPER_CONTROL_JOURNAL_DUPLICATE"):
        PortfolioCoordinator(config=cfg).start()
    assert not Path(cfg["paper_control_state_path"]).exists()
