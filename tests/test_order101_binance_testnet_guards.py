from __future__ import annotations

import math

import pytest

from senecio_polymarket.oracle.exchange_connector import ExchangeConnector


class FakeExchange:
    def __init__(
        self,
        *,
        sandbox_marker: bool,
        private_url: str,
    ):
        self._senex_testnet_sandbox_enabled = sandbox_marker
        self.urls = {"api": {"fapiPrivate": private_url}}
        self.create_calls = []

    def fetch_ticker(self, symbol):
        return {"last": 100.0}

    def create_market_order(self, symbol, side, amount, params=None):
        self.create_calls.append((symbol, side, amount, params))
        return {
            "id": "test-order-1",
            "status": "closed",
            "average": 100.0,
            "filled": amount,
            "cost": 100.0 * amount,
            "fees": [],
        }

    def fetch_order(self, order_id, symbol):
        return None


def _connector(name: str, exchange: FakeExchange) -> ExchangeConnector:
    connector = ExchangeConnector.__new__(ExchangeConnector)
    connector.exchanges = {name: exchange}
    return connector


@pytest.mark.parametrize(
    "name,sandbox_marker,private_url",
    [
        (
            "binance",
            True,
            "https://testnet.binancefuture.com/fapi/v1",
        ),
        (
            "binance_testnet",
            False,
            "https://testnet.binancefuture.com/fapi/v1",
        ),
        (
            "binance_testnet",
            True,
            "https://fapi.binance.com/fapi/v1",
        ),
        (
            "binance_testnet",
            False,
            "https://fapi.binance.com/fapi/v1",
        ),
    ],
)
def test_place_market_order_fails_closed_unless_all_testnet_guards_pass(
    name,
    sandbox_marker,
    private_url,
):
    exchange = FakeExchange(
        sandbox_marker=sandbox_marker,
        private_url=private_url,
    )
    connector = _connector(name, exchange)

    with pytest.raises(RuntimeError, match="TESTNET"):
        connector.place_market_order(name, "BTC/USDT", "buy", 0.001)

    assert exchange.create_calls == []


@pytest.mark.parametrize("side", ["", "BUY_NOW", "hold", None])
def test_place_market_order_rejects_invalid_side_before_exchange_call(side):
    exchange = FakeExchange(
        sandbox_marker=True,
        private_url="https://testnet.binancefuture.com/fapi/v1",
    )
    connector = _connector("binance_testnet", exchange)

    with pytest.raises(ValueError, match="side"):
        connector.place_market_order(
            "binance_testnet",
            "BTC/USDT",
            side,
            0.001,
        )

    assert exchange.create_calls == []


@pytest.mark.parametrize(
    "amount",
    [0, -0.001, float("nan"), float("inf"), -float("inf"), True, None],
)
def test_place_market_order_rejects_nonpositive_or_nonfinite_amount(amount):
    exchange = FakeExchange(
        sandbox_marker=True,
        private_url="https://testnet.binancefuture.com/fapi/v1",
    )
    connector = _connector("binance_testnet", exchange)

    with pytest.raises(ValueError, match="amount"):
        connector.place_market_order(
            "binance_testnet",
            "BTC/USDT",
            "buy",
            amount,
        )

    assert exchange.create_calls == []


def test_place_market_order_accepts_fully_guarded_testnet_mock():
    exchange = FakeExchange(
        sandbox_marker=True,
        private_url="https://testnet.binancefuture.com/fapi/v1",
    )
    connector = _connector("binance_testnet", exchange)

    result = connector.place_market_order(
        "binance_testnet",
        "BTC/USDT",
        "BUY",
        0.001,
    )

    assert exchange.create_calls == [
        ("BTC/USDT", "buy", 0.001, {})
    ]
    assert result["exchange"] == "binance_testnet"
    assert result["is_testnet"] is True
    assert result["side"] == "buy"
    assert result["fill_amount"] == pytest.approx(0.001)
