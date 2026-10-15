from __future__ import annotations
import os, unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from senecio_polymarket.backend.binance_public_router import router

def fake_payload():
    return {
        "schema_version": 1, "status": "ok",
        "mode": "READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY",
        "venue": "Binance Spot", "symbol": "BTCUSDT", "interval": "4h",
        "requested_limit": 2, "server_time_utc": "2026-10-14T20:00:00.000Z",
        "fetched_at_utc": "2026-10-14T20:00:00.000Z",
        "source": {"raw_response_sha256": "a"*64,
                   "canonical_closed_candles_sha256": "b"*64,
                   "closed_rows_returned": 2, "incomplete_rows_dropped": 0,
                   "gap_count": 0, "gaps_filled": False},
        "latest_closed_candle": {"open_time_utc": "2026-10-14T12:00:00.000Z",
                                 "close_time_utc": "2026-10-14T15:59:59.999Z",
                                 "open": "100", "high": "105", "low": "98", "close": "102", "volume": "3"},
        "candles": [],
        "safety": {"api_key_used": False, "signed_requests_used": False,
                   "account_endpoints_called": False, "order_endpoints_called": 0,
                   "live_orders_submitted": 0, "testnet_orders_submitted": 0,
                   "live_trading_enabled": False, "EDGE": "UNPROVEN"},
    }

class BinancePublicRouterTests(unittest.TestCase):
    def setUp(self):
        self.app=FastAPI()
        self.app.include_router(router)
        self.client=TestClient(self.app)
        self.env=patch.dict(os.environ, {"SENEX_BINANCE_PUBLIC_DATA_ENABLED":"1"})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.client.close()

    def test_reads_public_klines_with_allowlisted_parameters(self):
        with patch("senecio_polymarket.backend.binance_public_market_data.get_public_klines",
                   return_value=fake_payload()) as fn:
            response=self.client.get("/api/binance/public_klines?symbol=btcusdt&interval=4h&limit=2")
        self.assertEqual(response.status_code,200)
        body=response.json()
        self.assertEqual(body["mode"],"READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY")
        self.assertFalse(body["safety"]["api_key_used"])
        self.assertEqual(body["safety"]["order_endpoints_called"],0)
        fn.assert_called_once_with("BTCUSDT","4h",2)

    def test_denies_non_allowlisted_symbol(self):
        response=self.client.get("/api/binance/public_klines?symbol=BNBUSDT&interval=4h&limit=2")
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()["detail"]["code"],"SYMBOL_NOT_ALLOWED")

    def test_denies_interval_outside_dashboard_allowlist(self):
        response=self.client.get("/api/binance/public_klines?symbol=BTCUSDT&interval=1m&limit=2")
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()["detail"]["code"],"INTERVAL_NOT_ALLOWED")

    def test_operator_can_disable_route_without_network_calls(self):
        with patch.dict(os.environ, {"SENEX_BINANCE_PUBLIC_DATA_ENABLED":"0"}):
            with patch("senecio_polymarket.backend.binance_public_market_data.get_public_klines") as fn:
                response=self.client.get("/api/binance/public_klines")
        self.assertEqual(response.status_code,503)
        fn.assert_not_called()

    def test_fails_closed_if_payload_claims_order_capability(self):
        unsafe=fake_payload()
        unsafe["safety"]["order_endpoints_called"]=1
        with patch("senecio_polymarket.backend.binance_public_market_data.get_public_klines",return_value=unsafe):
            response=self.client.get("/api/binance/public_klines")
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json()["detail"]["code"],"PUBLIC_MARKET_DATA_SAFETY_CONTRACT_FAILED")

    def test_upstream_rate_limit_is_preserved_as_safe_error(self):
        from senecio_polymarket.backend.binance_public_market_data import PublicMarketDataError
        with patch("senecio_polymarket.backend.binance_public_market_data.get_public_klines",
                   side_effect=PublicMarketDataError("UPSTREAM_RATE_LIMIT","wait",503)):
            response=self.client.get("/api/binance/public_klines")
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()["detail"]["code"],"UPSTREAM_RATE_LIMIT")
        self.assertFalse(response.json()["detail"]["live_trading_enabled"])

    def test_live_public_candles_run_clean_room_rea_diagnostic_only(self):
        from datetime import datetime, timezone, timedelta
        start_ms = 1_760_000_000_000
        candles = []
        for index in range(60):
            opened = datetime.fromtimestamp((start_ms + index * 14_400_000) / 1000, timezone.utc)
            closed = opened + timedelta(hours=4) - timedelta(milliseconds=1)
            price = 70_000 + index * 50
            candles.append({
                "open_time_utc": opened.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "close_time_utc": closed.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "open": str(price), "high": str(price + 120), "low": str(price - 80),
                "close": str(price + 20), "volume": "2.0",
            })
        payload = {
            "schema_version": 1, "status": "ok",
            "mode": "READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY",
            "venue": "Binance Spot", "symbol": "BTCUSDT", "interval": "4h",
            "requested_limit": 60, "server_time_utc": candles[-1]["close_time_utc"],
            "fetched_at_utc": candles[-1]["close_time_utc"],
            "source": {
                "endpoint": "/api/v3/klines", "url": "https://api.binance.com/api/v3/klines",
                "raw_response_sha256": "a" * 64, "canonical_closed_candles_sha256": "b" * 64,
                "raw_rows_returned": 60, "closed_rows_returned": 60,
                "incomplete_rows_dropped": 0, "gap_count": 0, "gaps": [], "gaps_filled": False,
            },
            "latest_closed_candle": candles[-1], "candles": candles,
            "safety": {
                "api_key_used": False, "signed_requests_used": False, "account_endpoints_called": False,
                "order_endpoints_called": 0, "live_orders_submitted": 0,
                "testnet_orders_submitted": 0, "live_trading_enabled": False, "EDGE": "UNPROVEN",
            },
            "cache_hit": True,
        }
        with patch("senecio_polymarket.backend.binance_public_market_data.get_public_klines",
                   return_value=payload) as fetch:
            response = self.client.get("/api/binance/rea_paper_diagnostic?symbol=BTCUSDT&interval=4h&limit=60")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed-paper-diagnostic")
        self.assertEqual(body["mode"], "PUBLIC_MARKET_DATA / PAPER_ONLY")
        self.assertEqual(set(body["strategies"]), {"grid", "dca", "combo"})
        self.assertIn("buy_and_hold_fixed_qty", body["benchmarks"])
        self.assertEqual(body["safety"]["EDGE"], "UNPROVEN")
        self.assertEqual(body["safety"]["order_endpoints_called"], 0)
        self.assertFalse(body["safety"]["runtime_execution_engine_touched"])
        self.assertFalse(body["safety"]["runtime_risk_kernel_touched"])
        self.assertFalse(body["safety"]["runtime_trade_journal_touched"])
        self.assertIn("cost_stress", body["strategies"]["dca"])
        fetch.assert_called_once_with("BTCUSDT", "4h", 60)

if __name__=="__main__": unittest.main()
