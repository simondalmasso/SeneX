import pytest

from senecio_polymarket.oracle.exchange_connector import ExchangeConnector


class _FakeMainnetExchange:
    urls = {
        "api": {
            "fapiPublic": "https://fapi.binance.com",
            "fapiPrivate": "https://fapi.binance.com",
        }
    }

    def fetch_ticker(self, symbol):
        raise AssertionError("order path reached before testnet URL validation")

    def create_market_order(self, symbol, side, amount, params=None):
        raise AssertionError("mainnet order method must never be reached")


def test_place_market_order_rejects_mainnet_url_even_under_testnet_alias():
    connector = ExchangeConnector.__new__(ExchangeConnector)
    connector.exchanges = {"binance_testnet": _FakeMainnetExchange()}

    with pytest.raises(RuntimeError, match="SAFETY ABORT"):
        connector.place_market_order(
            "binance_testnet",
            "BTC/USDT",
            "buy",
            0.001,
        )


class _FakeVerifiedTestnetExchange:
    urls = {
        "api": {
            "fapiPublic": "https://testnet.binancefuture.com/fapi/v1",
            "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
        }
    }

    def __init__(self):
        self.order_calls = 0

    def fetch_ticker(self, symbol):
        return {"last": 100.0}

    def create_market_order(self, symbol, side, amount, params=None):
        self.order_calls += 1
        return {
            "id": "testnet-order-1",
            "status": "closed",
            "average": 100.0,
            "filled": amount,
            "cost": 100.0 * amount,
            "fees": [],
        }

    def fetch_order(self, order_id, symbol):
        return {
            "id": order_id,
            "status": "closed",
            "average": 100.0,
            "filled": 0.001,
            "cost": 0.1,
            "fees": [],
        }


def test_place_market_order_allows_verified_testnet_urls():
    connector = ExchangeConnector.__new__(ExchangeConnector)
    exchange = _FakeVerifiedTestnetExchange()
    connector.exchanges = {"binance_testnet": exchange}

    result = connector.place_market_order(
        "binance_testnet",
        "BTC/USDT",
        "buy",
        0.001,
    )

    assert exchange.order_calls == 1
    assert result["exchange"] == "binance_testnet"
    assert result["is_testnet"] is True
    assert result["order_id"] == "testnet-order-1"
