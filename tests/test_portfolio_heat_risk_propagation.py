import asyncio
import pytest

from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine
from senecio_polymarket.backend.portfolio.portfolio_engine import PortfolioEngine, TradeProposal
from senecio_polymarket.backend.portfolio.risk_kernel import RiskDecision


def test_filled_position_preserves_scaled_risk_for_portfolio_heat():
    execution = ExecutionEngine(config={
        "latency_ms_min": 0,
        "latency_ms_max": 0,
        "max_retries": 0,
        "book_depth_assumed_usd": 1_000_000.0,
    })
    proposal = TradeProposal(
        symbol="BTCUSDT", direction="LONG", size_usd=1000.0, size_qty=1.0,
        entry_price=1000.0, stop_price=900.0, target_price=1200.0,
        risk_per_unit=100.0, risk_usd=200.0, confidence=0.8, ev=0.01,
        prediction_id="risk-prop",
    )
    decision = RiskDecision(approved=True, reason="test", size_scale=0.5, proposal_id="risk-prop")
    order = asyncio.run(execution.submit(
        proposal=proposal,
        decision=decision,
        last_price=1000.0,
        book_depth_usd=1_000_000.0,
    ))
    assert order.filled_qty > 0
    pos = execution.positions["BTCUSDT"]
    expected_risk = proposal.risk_usd * decision.size_scale * (order.filled_qty / order.ordered_qty)
    assert pos.risk_usd == pytest.approx(expected_risk, abs=0.01)

    state = PortfolioEngine().recompute_state(
        open_positions={"BTCUSDT": pos.to_dict()},
        cash=execution.cash,
        starting_equity=10_000.0,
        last_prices={"BTCUSDT": 1000.0},
    )
    assert state.portfolio_heat_pct == pytest.approx(expected_risk / 10_000.0, abs=1e-4)
