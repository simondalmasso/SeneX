from senecio_polymarket.backend.portfolio.meta_labeler import MetaLabeler
from senecio_polymarket.backend.portfolio.portfolio_engine import PortfolioEngine, PortfolioState


def test_meta_labeler_rejects_non_positive_expected_ev():
    label = MetaLabeler().evaluate(
        direction="LONG", conviction=0.9, regime_4h="BULL", vol_pct=0.01,
        spread_bps=1.0, entry_price=100.0, stop_price=98.0,
        target_price=104.0, expected_ev_bps=-100.0,
    )
    assert label.take_trade is False
    assert "ev" in label.reason.lower()


def test_portfolio_engine_does_not_launder_negative_ev_with_abs():
    engine = PortfolioEngine()
    engine.meta_labeler = MetaLabeler()
    state = PortfolioState(equity=10_000.0, cash=10_000.0)
    prediction = {
        "id": "negative-ev", "symbol": "BTCUSDT", "prediction": "LONG",
        "price_now": 100.0, "confidence": 0.9, "ev": -0.01,
        "_audit": {"regime_4h": "BULL", "spread_bps": 1.0},
    }
    proposal = engine.build_proposal(
        prediction=prediction, state=state, vol_pct=0.01,
        win_rate_by_direction={"LONG": 0.60},
    )
    assert proposal is None
