import asyncio
from unittest.mock import patch

from senecio_polymarket.backend.portfolio.coordinator import PortfolioCoordinator


def test_coordinator_updates_riskkernel_vol_regime_before_risk_evaluation(tmp_path):
    coord = PortfolioCoordinator(config={"journal_path": str(tmp_path / "trades.jsonl")})
    coord.start()
    prediction = {
        "id": "vol-high",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "price_now": 100.0,
        "confidence": 0.9,
        "ev": 0.02,
    }

    with patch.object(coord.portfolio_engine, "build_proposal", return_value=None):
        asyncio.run(coord.ingest_prediction(
            prediction=prediction,
            last_price=100.0,
            vol_pct=0.04,
        ))

    assert coord.risk_kernel.state.vol_pct == 0.04
    assert coord.risk_kernel.state.vol_regime == "HIGH"
