from senecio_polymarket.backend.portfolio.execution_fidelity import (
    BookLevel,
    BookSnapshot,
    FillSimulator,
)


def test_missing_book_fails_closed_without_synthetic_dollar_fill():
    sim = FillSimulator()
    est = sim.simulate_fill(side="BUY", notional_usd=1000.0, book=None, is_marketable=True)

    assert est.expected_qty == 0.0
    assert est.expected_vwap_price == 0.0
    assert est.book_present is False


def test_one_sided_book_with_no_buy_liquidity_fails_closed():
    sim = FillSimulator()
    book = BookSnapshot(
        symbol="BTCUSDT",
        bids=[BookLevel(price=65000.0, size=1.0)],
        asks=[],
        last_trade_price=65000.0,
    )
    est = sim.simulate_fill(side="BUY", notional_usd=1000.0, book=book, is_marketable=True)

    assert est.expected_qty == 0.0
    assert est.expected_vwap_price == 0.0
    assert est.book_present is True
