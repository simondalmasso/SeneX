import asyncio
from unittest.mock import patch

from senecio_polymarket.backend import oracle_runner


class _Cfg:
    def update_config(self, **kwargs):
        return None


class _CaptureCoordinator:
    def __init__(self):
        self.portfolio_engine = _Cfg()
        self.risk_kernel = _Cfg()
        self.execution_engine = _Cfg()
        self.kwargs = None

    def on_tick(self, symbol, price, ts=None):
        return []

    async def ingest_prediction(self, **kwargs):
        self.kwargs = kwargs
        return {"skipped": "test"}


def test_synthetic_book_preserves_observed_best_bid_and_ask():
    coord = _CaptureCoordinator()
    prediction = {"id": "p1", "symbol": "BTCUSDT", "prediction": "LONG", "price_now": 100.0}
    market_data = {
        "ohlcv": [],
        "feature_observations": {},
        "funding": {},
        "open_interest": {},
        "orderbook": {"bid_depth": 10000.0, "ask_depth": 12000.0},
        "ticker": {"bid": 99.0, "ask": 101.0},
    }
    with patch.object(oracle_runner, "_get_portfolio_coordinator", return_value=coord):
        asyncio.run(oracle_runner._route_to_portfolio(prediction, market_data))

    book = coord.kwargs["orderbook"]
    assert book["bids"][0][0] == 99.0
    assert book["asks"][0][0] == 101.0
    assert book["asks"][0][0] > book["bids"][0][0]
