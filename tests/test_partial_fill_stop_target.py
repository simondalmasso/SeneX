import asyncio
from unittest.mock import AsyncMock, patch

from senecio_polymarket.backend.portfolio.coordinator import PortfolioCoordinator
from senecio_polymarket.backend.portfolio.execution_engine import Order, Position
from senecio_polymarket.backend.portfolio.portfolio_engine import TradeProposal
from senecio_polymarket.backend.portfolio.risk_kernel import RiskDecision


def test_partial_filled_position_receives_proposal_stop_and_target(tmp_path):
    coord = PortfolioCoordinator(config={"journal_path": str(tmp_path / "trades.jsonl")})
    coord.start()

    proposal = TradeProposal(
        symbol="BTCUSDT", direction="LONG", size_usd=1000.0, size_qty=0.01,
        entry_price=100000.0, stop_price=98000.0, target_price=104000.0,
        risk_per_unit=2000.0, risk_usd=20.0, confidence=0.8, ev=0.01,
        prediction_id="pred-partial",
    )
    decision = RiskDecision(approved=True, reason="test", proposal_id="pred-partial")
    partial_order = Order(
        order_id="ord-partial", client_order_id="cli-partial", symbol="BTCUSDT",
        side="BUY", direction="LONG", ordered_qty=0.01, filled_qty=0.004,
        avg_fill_price=100000.0, limit_price=100000.0, status="CANCELED",
        proposal_id="pred-partial",
    )
    coord.execution_engine.positions["BTCUSDT"] = Position(
        position_id="pos-partial", symbol="BTCUSDT", direction="LONG", qty=0.004,
        avg_entry_price=100000.0, entry_ts="2026-09-12T06:00:00+00:00",
        stop_price=0.0, target_price=0.0, proposal_id="pred-partial",
    )

    with patch.object(coord.portfolio_engine, "build_proposal", return_value=proposal), \
         patch.object(coord.risk_kernel, "evaluate", return_value=decision), \
         patch.object(coord.execution_engine, "submit", new=AsyncMock(return_value=partial_order)):
        asyncio.run(coord.ingest_prediction(
            prediction={"id": "pred-partial", "symbol": "BTCUSDT", "prediction": "LONG", "price_now": 100000.0},
            last_price=100000.0,
        ))
    pos = coord.execution_engine.positions["BTCUSDT"]
    assert pos.stop_price == 98000.0
    assert pos.target_price == 104000.0
