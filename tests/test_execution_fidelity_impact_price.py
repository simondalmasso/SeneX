import pytest

from senecio_polymarket.backend.portfolio.execution_fidelity import (
    BookLevel,
    BookSnapshot,
    FillSimulator,
)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_reported_slippage_is_charged_to_expected_fill_price(side):
    sim = FillSimulator(config={"impact_coeff": 0.50, "adv_assumed_usd": 100_000.0})
    book = BookSnapshot(
        symbol="BTCUSDT",
        bids=[BookLevel(price=99.0, size=100.0)],
        asks=[BookLevel(price=101.0, size=100.0)],
        last_trade_price=100.0,
        toxic_flow_score=1.0,
    )

    est = sim.simulate_fill(side=side, notional_usd=1000.0, book=book, is_marketable=True)
    mid = book.mid()
    realized_slip_bps = abs(est.expected_vwap_price - mid) / mid * 10_000

    assert realized_slip_bps == pytest.approx(est.expected_slippage_bps, abs=0.05)
