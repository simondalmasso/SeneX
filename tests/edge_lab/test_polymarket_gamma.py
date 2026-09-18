from edge_lab.adapters.polymarket_gamma import parse_btc_hourly_contract


def test_parse_official_hourly_gamma_market_contract():
    payload = {
        "slug": "bitcoin-up-or-down-september-18-2026-1am-et",
        "question": "Bitcoin Up or Down - September 18, 1AM ET",
        "conditionId": "0xabc",
        "eventStartTime": "2026-09-18T05:00:00Z",
        "endDate": "2026-09-18T06:00:00Z",
        "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
        "outcomes": "[\"Up\", \"Down\"]",
        "clobTokenIds": "[\"1\", \"2\"]",
        "description": (
            'This market will resolve to "Up" if the close price is greater than '
            'or equal to the open price for the BTC/USDT 1 hour candle that begins '
            'on the time and date specified in the title. Otherwise, this market '
            'will resolve to "Down".'
        ),
    }

    contract = parse_btc_hourly_contract(payload)

    assert contract.asset == "BTC/USDT"
    assert contract.start_ts == "2026-09-18T05:00:00Z"
    assert contract.end_ts == "2026-09-18T06:00:00Z"
    assert contract.data_source == "binance"
    assert contract.tie_semantics == "UP_ON_EQUAL"
    assert contract.condition_id == "0xabc"


def test_gamma_contract_parser_fails_closed_on_wrong_resolution_source():
    payload = {
        "slug": "bitcoin-up-or-down-test",
        "question": "Bitcoin Up or Down - test",
        "conditionId": "0xabc",
        "eventStartTime": "2026-09-18T05:00:00Z",
        "endDate": "2026-09-18T06:00:00Z",
        "resolutionSource": "https://example.com",
        "outcomes": "[\"Up\", \"Down\"]",
        "clobTokenIds": "[\"1\", \"2\"]",
        "description": "BTC/USDT 1 hour candle close greater than or equal to open.",
    }

    try:
        parse_btc_hourly_contract(payload)
    except ValueError as exc:
        assert "Binance BTC/USDT" in str(exc)
    else:
        raise AssertionError("expected non-Binance resolution source to fail")
