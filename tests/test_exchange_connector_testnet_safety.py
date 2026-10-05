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
