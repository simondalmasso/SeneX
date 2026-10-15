from __future__ import annotations
import json, unittest
from urllib.parse import parse_qs, urlparse
from senecio_polymarket.backend import binance_public_market_data as mod

def row(open_ms, close_ms, open_="100", high="105", low="95", close="101", volume="7"):
    return [open_ms,open_,high,low,close,volume,close_ms,"700","15","2","200","0"]

class BinancePublicMarketDataTests(unittest.TestCase):
    def setUp(self):
        mod.reset_cache_for_tests()
    def test_public_get_only_closed_candles_and_no_credentials(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,start+3_599_999),row(start+3_600_000,start+7_199_999),
                            row(start+7_200_000,start+10_799_999)]).encode()
        calls=[]
        def fake(url,timeout):
            calls.append(url); p=urlparse(url)
            if p.path==mod.KLINES_PATH:
                self.assertEqual(parse_qs(p.query)["symbol"],["BTCUSDT"])
                self.assertEqual(parse_qs(p.query)["limit"],["3"])
                self.assertNotIn("signature",parse_qs(p.query))
                return 200,payload
            if p.path==mod.SERVER_TIME_PATH:return 200,json.dumps({"serverTime":start+7_200_000}).encode()
            raise AssertionError(p.path)
        result=mod.get_public_klines("btcusdt","1h",3,transport=fake,monotonic_fn=lambda:100.0)
        self.assertEqual(len(calls),2)
        self.assertEqual(result["source"]["closed_rows_returned"],2)
        self.assertEqual(result["source"]["incomplete_rows_dropped"],1)
        self.assertFalse(result["safety"]["api_key_used"])
        self.assertEqual(result["safety"]["order_endpoints_called"],0)
        self.assertEqual(result["latest_closed_candle"]["close"],"101")
    def test_cache_hit_does_not_repeat_http(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,start+3_599_999),row(start+3_600_000,start+7_199_999)]).encode()
        count={"calls":0}
        def fake(url,timeout):
            count["calls"]+=1
            if urlparse(url).path==mod.KLINES_PATH:return 200,payload
            return 200,json.dumps({"serverTime":start+8_000_000}).encode()
        first=mod.get_public_klines("BTCUSDT","1h",2,transport=fake,monotonic_fn=lambda:100)
        second=mod.get_public_klines("BTCUSDT","1h",2,transport=fake,monotonic_fn=lambda:101)
        self.assertFalse(first["cache_hit"]);self.assertTrue(second["cache_hit"])
        self.assertEqual(count["calls"],2)
    def test_gap_is_reported_not_filled(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,start+3_599_999),row(start+7_200_000,start+10_799_999)]).encode()
        def fake(url,timeout):
            if urlparse(url).path==mod.KLINES_PATH:return 200,payload
            return 200,json.dumps({"serverTime":start+20_000_000}).encode()
        result=mod.get_public_klines("BTCUSDT","1h",2,transport=fake,monotonic_fn=lambda:100)
        self.assertEqual(result["source"]["gap_count"],1)
        self.assertFalse(result["source"]["gaps_filled"])
        self.assertFalse(result["source"]["gaps"][0]["filled_or_interpolated"])
    def test_http_429_is_fail_closed(self):
        with self.assertRaisesRegex(mod.PublicMarketDataError,"HTTP 429"):
            mod.get_public_klines("BTCUSDT","1h",2,transport=lambda url,timeout:(429,b"{}"),monotonic_fn=lambda:100)
    def test_validation_caps_symbols_intervals_and_limit(self):
        with self.assertRaises(mod.PublicMarketDataError):mod.get_public_klines("BTCUSDT","bad",2,monotonic_fn=lambda:100)
        with self.assertRaises(mod.PublicMarketDataError):mod.get_public_klines("BTCUSDT","1h",101,monotonic_fn=lambda:100)
    def test_snapshot_has_no_private_or_trade_capabilities(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,start+3_599_999),row(start+3_600_000,start+7_199_999)]).encode()
        def fake(url,timeout):
            return (200,payload) if urlparse(url).path==mod.KLINES_PATH else (200,json.dumps({"serverTime":start+8_000_000}).encode())
        result=mod.get_public_klines("BTCUSDT","1h",2,transport=fake,monotonic_fn=lambda:100)
        self.assertFalse(result["safety"]["signed_requests_used"])
        self.assertFalse(result["safety"]["account_endpoints_called"])
        self.assertEqual(result["safety"]["live_orders_submitted"],0)
        self.assertEqual(result["safety"]["testnet_orders_submitted"],0)

if __name__=="__main__":unittest.main()
