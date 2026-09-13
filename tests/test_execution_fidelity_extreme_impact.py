from senecio_polymarket.backend.portfolio.execution_fidelity import (
    BookLevel,
    BookSnapshot,
    FillSimulator,
)


def test_extreme_sell_impact_fails_closed_instead_of_negative_price():
    sim = FillSimulator(config={"impact_coeff": 100.0, "adv_assumed_usd": 1.0})
    book = BookSnapshot(
        symbol="BTCUSDT",
        bids=[BookLevel(price=99.0, size=100.0)],
        asks=[BookLevel(price=101.0, size=100.0)],
        last_trade_price=100.0,
    )
    est = sim.simulate_fill(
        side="SELL", notional_usd=1000.0, book=book, is_marketable=True
    )

    assert est.expected_qty == 0.0
    assert est.expected_vwap_price == 0.0
    assert est.expected_fee_usd == 0.0
    assert est.model == "fail_closed_no_fill"
    assert est.detail["reason"] == "non_positive_effective_price"


def test_extreme_buy_impact_fails_closed_instead_of_absurd_price():
    sim = FillSimulator(config={"impact_coeff": 100.0, "adv_assumed_usd": 1.0})
    book = BookSnapshot(
        symbol="BTCUSDT",
        bids=[BookLevel(price=99.0, size=100.0)],
        asks=[BookLevel(price=101.0, size=100.0)],
        last_trade_price=100.0,
    )
    est = sim.simulate_fill(
        side="BUY", notional_usd=1000.0, book=book, is_marketable=True
    )

    assert est.expected_qty == 0.0
    assert est.expected_vwap_price == 0.0
    assert est.expected_slippage_bps == 0.0
    assert est.expected_market_impact_bps == 0.0
    assert est.expected_fee_usd == 0.0
    assert est.model == "fail_closed_no_fill"
    assert est.detail["reason"] == "excessive_effective_slippage"
