"""Regression test for real oracle -> portfolio tick wiring."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from senecio_polymarket.backend import oracle_runner


class _Cfg:
    def update_config(self, **kwargs):
        return None


class _FakeCoordinator:
    def __init__(self):
        self.portfolio_engine = _Cfg()
        self.risk_kernel = _Cfg()
        self.execution_engine = _Cfg()
        self.events = []

    def on_tick(self, symbol, price, ts=None):
        self.events.append(("tick", symbol, price))
        return []
    async def ingest_prediction(self, **kwargs):
        self.events.append(("ingest", kwargs["prediction"]["symbol"], kwargs["last_price"]))
        return {"skipped": "test"}


def test_route_ticks_existing_positions_before_ingesting_new_prediction():
    coord = _FakeCoordinator()
    prediction = {
        "id": "pred-1",
        "symbol": "BTCUSDT",
        "prediction": "HOLD",
        "price_now": 64000.0,
    }
    market_data = {
        "ohlcv": [],
        "feature_observations": {},
        "orderbook": {},
        "ticker": {"bid": 63999.0, "ask": 64001.0},
    }

    with patch.object(oracle_runner, "_get_portfolio_coordinator", return_value=coord):
        asyncio.run(oracle_runner._route_to_portfolio(prediction, market_data))

    assert coord.events[0] == ("tick", "BTCUSDT", 64000.0)
    assert coord.events[1] == ("ingest", "BTCUSDT", 64000.0)


def test_tick_failure_blocks_new_portfolio_ingest():
    coord = _FakeCoordinator()
    def _boom(*args, **kwargs):
        raise RuntimeError("exit subsystem failed")
    coord.on_tick = _boom
    prediction = {"id":"pred-2","symbol":"BTCUSDT","prediction":"LONG","price_now":64000.0}
    market_data = {"ohlcv":[],"feature_observations":{},"orderbook":{},"ticker":{"bid":63999.0,"ask":64001.0}}
    with patch.object(oracle_runner, "_get_portfolio_coordinator", return_value=coord):
        asyncio.run(oracle_runner._route_to_portfolio(prediction, market_data))
    assert not any(event[0] == "ingest" for event in coord.events)
