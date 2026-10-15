from __future__ import annotations
import hashlib, json, tempfile, unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from research.rea_engines import binance_spot_public as mod
from research.rea_engines.contracts import Candle

def row(open_ms: int, *, close_ms: int | None = None, open_: str = "100", high: str = "105",
        low: str = "95", close: str = "101", volume: str = "7") -> list[object]:
    return [open_ms, open_, high, low, close, volume,
            close_ms if close_ms is not None else open_ms + 3_599_999,
            "707", 19, "2.1", "212", "0"]

class BinancePublicKlineParserTests(unittest.TestCase):
    def test_parses_only_fully_closed_candles_and_hashes_raw_bytes(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,close_ms=start+3_599_999),
          row(start+3_600_000,close_ms=start+7_199_999),
          row(start+7_200_000,close_ms=start+10_799_999)],separators=(",",":")).encode()
        snap=mod.parse_klines_response(payload,symbol="btcusdt",interval="1h",limit=3,server_time_ms=start+7_200_000)
        self.assertEqual(snap.symbol,"BTCUSDT")
        self.assertEqual(snap.raw_rows_returned,3)
        self.assertEqual(len(snap.candles),2)
        self.assertEqual(snap.incomplete_candles_dropped,1)
        self.assertEqual(snap.candles[0].close, Candle(timestamp=snap.candles[0].timestamp,open="100",high="105",low="95",close="101",volume="7").close)
        self.assertEqual(snap.raw_response_sha256,hashlib.sha256(payload).hexdigest())
        self.assertEqual(len(snap.canonical_candles_sha256),64)

    def test_gap_is_reported_without_synthetic_interpolation(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,close_ms=start+3_599_999),row(start+7_200_000,close_ms=start+10_799_999)]).encode()
        snap=mod.parse_klines_response(payload,symbol="BTCUSDT",interval="1h",limit=2,server_time_ms=start+30_000_000)
        self.assertEqual(len(snap.candles),2)
        self.assertEqual(len(snap.gaps),1)
        self.assertEqual(snap.gaps[0].missing_intervals,1)
        self.assertFalse(snap.gaps[0].non_multiple_gap)
        self.assertFalse(snap.gaps[0].to_dict()["filled_or_interpolated"])

    def test_rejects_invalid_ohlc_contract(self):
        start=1_760_000_000_000
        with self.assertRaisesRegex(mod.BinancePublicDataError,"OHLCV contract"):
            mod.parse_klines_response(json.dumps([row(start,high="90",low="95")]).encode(),
                symbol="BTCUSDT",interval="1h",limit=1,server_time_ms=start+5_000_000)

    def test_rejects_non_monotonic_or_duplicate_open_times(self):
        start=1_760_000_000_000
        payload=json.dumps([row(start,close_ms=start+3_599_999),row(start,close_ms=start+3_599_999)]).encode()
        with self.assertRaisesRegex(mod.BinancePublicDataError,"strictly increasing"):
            mod.parse_klines_response(payload,symbol="BTCUSDT",interval="1h",limit=2,server_time_ms=start+10_000_000)

    def test_rejects_bad_kline_shape_and_invalid_json(self):
        with self.assertRaisesRegex(mod.BinancePublicDataError,"exactly 12"):
            mod.parse_klines_response(json.dumps([[1,2,3]]).encode(),symbol="BTCUSDT",interval="1h",limit=1,server_time_ms=10)
        with self.assertRaisesRegex(mod.BinancePublicDataError,"valid UTF-8 JSON"):
            mod.parse_klines_response(b"{bad",symbol="BTCUSDT",interval="1h",limit=1,server_time_ms=10)

    def test_rejects_invalid_parameters_and_empty_closed_sample(self):
        with self.assertRaises(mod.BinancePublicDataError):
            mod.parse_klines_response(b"[]",symbol="BTCUSDT",interval="bad",limit=1,server_time_ms=10)
        with self.assertRaises(mod.BinancePublicDataError):
            mod.parse_klines_response(b"[]",symbol="BTCUSDT",interval="1h",limit=1001,server_time_ms=10)
        start=1_760_000_000_000
        with self.assertRaisesRegex(mod.BinancePublicDataError,"no fully closed candles"):
            mod.parse_klines_response(json.dumps([row(start,close_ms=start+3_599_999)]).encode(),
                symbol="BTCUSDT",interval="1h",limit=1,server_time_ms=start+100)

    def test_rejects_kline_before_requested_start_time(self):
        start=1_760_000_000_000
        with self.assertRaisesRegex(mod.BinancePublicDataError,"precedes startTime"):
            mod.parse_klines_response(json.dumps([row(start,close_ms=start+3_599_999)]).encode(),
                symbol="BTCUSDT",interval="1h",limit=1,server_time_ms=start+5_000_000,start_time_ms=start+1)

class BinancePublicTransportTests(unittest.TestCase):
    def test_fetch_uses_only_public_get_endpoints_and_no_api_key(self):
        start=1_760_000_000_000
        body=json.dumps([row(start,close_ms=start+3_599_999),row(start+3_600_000,close_ms=start+7_199_999),
                        row(start+7_200_000,close_ms=start+10_799_999)]).encode()
        requests=[]
        def fake_get(url,timeout):
            requests.append((url,timeout)); parsed=urlparse(url)
            if parsed.path==mod.KLINES_PATH:
                params=parse_qs(parsed.query)
                self.assertEqual(params["symbol"],["BTCUSDT"])
                self.assertEqual(params["interval"],["1h"])
                self.assertEqual(params["limit"],["3"])
                self.assertNotIn("signature",params)
                return 200,body
            if parsed.path==mod.SERVER_TIME_PATH: return 200,json.dumps({"serverTime":start+7_200_000}).encode()
            raise AssertionError("unexpected endpoint "+parsed.path)
        snap=mod.fetch_public_klines("BTCUSDT","1h",3,start_time_ms=start,transport=fake_get)
        self.assertEqual(len(requests),2)
        self.assertEqual(len(snap.candles),2)
        self.assertTrue(all(url.startswith(mod.API_BASE_URL) for url,_ in requests))
        self.assertTrue(all(timeout>0 for _,timeout in requests))
        self.assertFalse(mod.LIVE_TRADING_ENABLED);self.assertFalse(mod.API_KEY_USED);self.assertEqual(mod.ORDER_ENDPOINT_CALLS,0)

    def test_transport_failure_fails_closed(self):
        with self.assertRaisesRegex(mod.BinancePublicDataError,"HTTP 429"):
            mod.fetch_public_klines("BTCUSDT","1h",2,transport=lambda url,timeout:(429,b'{"code":-1003,"msg":"Too many requests"}'))

class BinanceBundleAndPaperEvaluationTests(unittest.TestCase):
    def _snapshot(self):
        start=1_760_000_000_000; rows=[]
        for i in range(60):
            op=start+i*14_400_000; price=70_000+i*50
            rows.append(row(op,close_ms=op+14_399_999,open_=str(price),high=str(price+120),low=str(price-80),close=str(price+20),volume="2.0"))
        raw=json.dumps(rows).encode()
        return mod.parse_klines_response(raw,symbol="BTCUSDT",interval="4h",limit=60,server_time_ms=start+60*14_400_000+1)

    def test_paper_evaluation_is_explicitly_fail_closed(self):
        report=mod.evaluate_snapshot_paper(self._snapshot())
        self.assertEqual(report["mode"],"PAPER_ONLY")
        self.assertEqual(report["safety"]["EDGE"],"UNPROVEN")
        self.assertFalse(report["safety"]["api_key_used"])
        self.assertEqual(report["safety"]["order_endpoints_called"],0)
        self.assertEqual(report["safety"]["live_orders_submitted"],0)
        self.assertEqual(set(report["rea_evaluation"]["strategies"]),{"grid","dca","combo"})
        self.assertIn("not a validated long-horizon OOS",report["evaluation_kind"])

    def test_bundle_writes_hash_bound_csv_raw_manifest_and_paper_report(self):
        snap=self._snapshot(); paper=mod.evaluate_snapshot_paper(snap)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/"BTCUSDT-4h.csv"
            files=mod.write_research_bundle(snap,output,paper)
            for name in ["ohlcv_csv","raw_response","manifest","paper_report"]: self.assertTrue(Path(files[name]).is_file())
            manifest=json.loads(Path(files["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifacts"]["ohlcv_csv"]["sha256"],hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(manifest["artifacts"]["raw_klines_response"]["sha256"],snap.raw_response_sha256)
            with self.assertRaisesRegex(mod.BinancePublicDataError,"refusing to overwrite"):
                mod.write_research_bundle(snap,output,paper)

    def test_bundle_rejects_non_csv_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mod.BinancePublicDataError,"end in .csv"):
                mod.write_research_bundle(self._snapshot(),Path(tmp)/"data.json")

if __name__=="__main__": unittest.main()
