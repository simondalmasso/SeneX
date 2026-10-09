"""M17 fixture-only prospective readiness. Synthetic observations are NEVER real evidence."""
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from research.edge.oracle_aligned_net_ev.book_costs import (
    CostError, hold_quote_net, walk_asks,
)
from research.edge.oracle_aligned_net_ev.custody_store import (
    AppendOnlyEvidence, IntegrityError,
)
from research.edge.oracle_aligned_net_ev.capture_offline import (
    OfflineCapture, CaptureError,
)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "m17_fixture"
        self.log = AppendOnlyEvidence(self.root)
        self.capture = OfflineCapture(self.log)

    def slot(self, start=300000):
        return {"market_id":"m1","condition_id":"c1","market_slug":"btc-updown-5m-300",
                "token_id_yes":"y1","token_id_no":"n1","oracle_source_id":"btc-5m-twap-60",
                "start_ms":start,"end_ms":start+300000}

    def samples(self, now=310000):
        book = lambda token: json.dumps(
            {"asset_id":token,"timestamp":str(now-25),
             "bids":[{"price":"0.49","size":"2.000000000000000001"}],
             "asks":[{"price":"0.51","size":"2.000000000000000001"}],
             "hash":"bookhash","last_trade_price":"0.50"},
            separators=(",",":")).encode()
        fee = lambda token: json.dumps(
            {"token_id":token,"feeSchedule":{"rate":"0.07","exponent":1}},
            separators=(",",":")).encode()
        shared=dict(self.slot(),rule_version="synthetic-r1")
        rule=dict(shared,exact_oracle_source="btc-5m-twap-60",
                  source_window_s=60,settlement_basis="CHAINLINK_BTC_USD_TWAP60",
                  tie_handling="UP_ON_EQUAL")
        data={"market_metadata":json.dumps(shared,separators=(",",":")).encode(),
              "market_rule":json.dumps(rule,separators=(",",":")).encode(),
              "book_yes":book("y1"),"book_no":book("n1"),
              "fee_yes":fee("y1"),"fee_no":fee("n1"),
              "senex_signal":b'{"prediction_id":"s1","horizon_s":300,"market_id":"m1","produced_at_ms":309950,"score_decimal":"0.53"}'}
        clocks={"book_yes":{"event_ms":now-25,"received_ms":now-10,"seq":10,"previous_seq":9,"dropped":0},
                "book_no":{"event_ms":now-25,"received_ms":now-10,"seq":11,"previous_seq":10,"dropped":0},
                "fee_yes":{"event_ms":now-20,"received_ms":now-5},
                "fee_no":{"event_ms":now-20,"received_ms":now-5},
                "senex_signal":{"event_ms":now-50,"received_ms":now-30}}
        signal={"prediction_id":"s1","horizon_s":300,"market_id":"m1",
                "produced_at_ms":now-50,"score_decimal":"0.53"}
        return data,clocks,signal

    def test_raw_bytes_append_only_survive_restart(self):
        data,clocks,signal=self.samples()
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertEqual(rec["kind"],"T0_SLOT")
        self.assertEqual(rec["phase"],"FIXTURE_OFFLINE")
        self.assertFalse(rec["source_admissible"])
        self.assertEqual(rec["flags"],[])
        self.assertEqual(rec["artifacts"]["book_yes"]["sha256"],
                         __import__("hashlib").sha256(data["book_yes"]).hexdigest())
        self.assertEqual(self.log.read_bytes(rec["artifacts"]["book_yes"]),data["book_yes"])
        recovered=AppendOnlyEvidence(self.root)
        self.assertEqual(len(recovered.verify()),1)
        self.assertEqual(recovered.verify()[0]["chain_hash"],rec["chain_hash"])

    def test_duplicate_and_post_outcome_t0_rejected(self):
        data,clocks,signal=self.samples()
        self.capture.record_t0(self.slot(),received_at_ms=310000,
                               raw_sources=data,source_clocks=clocks,signal=signal)
        with self.assertRaises(IntegrityError):
            self.capture.record_t0(self.slot(),received_at_ms=311000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)
        with self.assertRaises(CaptureError):
            self.capture.record_t0(self.slot(600000),received_at_ms=900000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)

    def test_missing_window_not_backfilled_and_abstains(self):
        data,clocks,signal=self.samples()
        self.capture.record_t0(self.slot(),received_at_ms=310000,
                               raw_sources=data,source_clocks=clocks,signal=signal)
        missed=self.capture.record_missing_window(self.slot(600000),detected_at_ms=920000)
        self.assertEqual(missed["kind"],"MISSED_WINDOW")
        self.assertEqual(missed["flags"],["MISSED_WINDOW","ABSTAIN"])
        with self.assertRaises(IntegrityError):
            self.capture.record_t0(self.slot(600000),received_at_ms=620000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)
        # Even a post-deadline monitor cannot turn a missed slot into old T0.
        self.assertEqual(len(self.log.verify()),2)

    def test_abstentions_record_all_missing_and_stale(self):
        data,clocks,signal=self.samples()
        data.pop("book_no");data.pop("fee_no")
        data.pop("senex_signal")
        clocks["book_yes"].update(event_ms=300000,seq=15,dropped=3)
        changed=json.loads(data["book_yes"])
        changed["timestamp"]="300000"
        data["book_yes"]=json.dumps(changed,separators=(",",":")).encode()
        result=self.capture.record_t0(self.slot(),received_at_ms=310000,
                                      raw_sources=data,source_clocks=clocks,signal=None,
                                      max_age_ms=500)
        for flag in ("NO_SIGNAL","NO_BOOK","NO_FEE","STALE","GAP","ABSTAIN"):
            self.assertIn(flag,result["flags"])
        self.assertEqual(result["eligible"],False)
        self.assertEqual(result["strategy_pnl_decimal"],"0")
        self.assertEqual(result["window_denominator"],1)

    def test_signal_claim_must_be_bound_to_original_source_bytes(self):
        data,clocks,signal=self.samples()
        # Caller declaration must not fabricate a valid source signal.
        data["senex_signal"]=b'{"prediction_id":"s1","horizon_s":300}'
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                    raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("NO_SIGNAL",rec["flags"])
        self.assertFalse(rec["signal_horizon_verified_5m"])

    def test_unknown_sequence_continuity_forces_gap(self):
        data,clocks,signal=self.samples()
        clocks["book_yes"].pop("previous_seq")
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                    raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("GAP",rec["flags"])
        self.assertIn("ABSTAIN",rec["flags"])

    def test_aud_inconsistent_market_rule_metadata_forces_abstain(self):
        data, clocks, signal = self.samples()
        data["market_metadata"] = b'{"market_id":"OTHER","condition_id":"c1","token_id_yes":"bad"}'
        data["market_rule"] = b'{"exact_oracle_source":"binance-spot"}'
        try:
            rec = self.capture.record_t0(self.slot(),received_at_ms=310000,
                   raw_sources=data,source_clocks=clocks,signal=signal)
        except CaptureError:
            return
        self.assertIn("NO_MARKET_RULE", rec["flags"])
        self.assertIn("ABSTAIN", rec["flags"])
        self.assertFalse(rec["fixture_inputs_complete"])

    def test_aud_empty_levels_not_valid_books(self):
        data, clocks, signal = self.samples()
        for name in ("book_yes", "book_no"):
            body = json.loads(data[name])
            body["asks"] = []
            body["bids"] = []
            data[name] = json.dumps(body).encode()
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                 raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("NO_BOOK",rec["flags"])
        self.assertIn("ABSTAIN",rec["flags"])
        self.assertFalse(rec["fixture_inputs_complete"])

    def test_aud_fractional_timestamp_must_not_truncate(self):
        data,clocks,signal=self.samples()
        for name in ("book_yes","book_no"):
            book=json.loads(data[name])
            book["timestamp"]=309975.9
            data[name]=json.dumps(book).encode()
        with self.assertRaises(CaptureError):
            self.capture.record_t0(self.slot(),received_at_ms=310000,
                 raw_sources=data,source_clocks=clocks,signal=signal)

    def test_invalid_book_price_and_size_force_no_book(self):
        data,clocks,signal=self.samples()
        book=json.loads(data["book_yes"])
        book["asks"]=[{"price":"NaN","size":"-10"}]
        data["book_yes"]=json.dumps(book).encode()
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                 raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("NO_BOOK",rec["flags"])

    def test_insufficient_best_ask_depth_fails_closed(self):
        data,clocks,signal=self.samples()
        book=json.loads(data["book_no"])
        book["asks"]=[{"price":"0.51","size":"0.00001"}]
        data["book_no"]=json.dumps(book).encode()
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                 raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("NO_BOOK",rec["flags"])

    def test_t0_requires_two_token_identity(self):
        data,clocks,signal=self.samples()
        bad=self.slot();bad["token_id_no"]="y1"
        with self.assertRaises(CaptureError):
            self.capture.record_t0(bad,received_at_ms=310000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)

    def test_signal_mismatched_horizon_is_no_signal(self):
        data,clocks,signal=self.samples()
        signal["horizon_s"]=3600
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)
        self.assertIn("NO_SIGNAL",rec["flags"])
        self.assertIn("ABSTAIN",rec["flags"])
        self.assertFalse(rec["eligible"])

    def test_orphan_after_crash_rejects_startup(self):
        (self.root/"blobs"/"orphan.raw").write_bytes(b"crash-before-journal")
        with self.assertRaises(IntegrityError):
            AppendOnlyEvidence(self.root)

    def test_tamper_rejected_on_restart(self):
        data,clocks,signal=self.samples()
        rec=self.capture.record_t0(self.slot(),received_at_ms=310000,
                                   raw_sources=data,source_clocks=clocks,signal=signal)
        p=self.root/"blobs"/rec["artifacts"]["book_yes"]["file"]
        p.write_bytes(b'{"forged":"changed"}')
        with self.assertRaises(IntegrityError):
            AppendOnlyEvidence(self.root)

    def test_truncated_journal_rejected(self):
        data,clocks,signal=self.samples()
        self.capture.record_t0(self.slot(),received_at_ms=310000,
                               raw_sources=data,source_clocks=clocks,signal=signal)
        p=self.root/"journal.jsonl"
        with p.open("ab") as f:f.write(b'{"incomplete":')
        with self.assertRaises(IntegrityError):
            AppendOnlyEvidence(self.root)

    def test_reconnect_gap_is_append_only_and_breaks_sequence(self):
        reconnect=self.capture.record_transport_event(
            kind="RECONNECT",received_at_ms=300001,connection_id="conn-a",
            raw_frame=b'{"type":"connected"}')
        gap=self.capture.record_transport_event(
            kind="GAP",received_at_ms=300010,connection_id="conn-a",
            raw_frame=b'{"seq":14}',previous_seq=10,current_seq=14)
        self.assertEqual([x["kind"] for x in self.log.verify()],["RECONNECT","GAP"])
        self.assertTrue(gap["has_gap"])
        self.assertEqual(reconnect["chain_hash"],gap["prev_hash"])

    def test_t1_is_separate_and_never_authorizes_source(self):
        data,clocks,signal=self.samples()
        self.capture.record_t0(self.slot(),received_at_ms=310000,
                               raw_sources=data,source_clocks=clocks,signal=signal)
        t1=self.capture.record_t1(self.slot(),received_at_ms=610000,
              raw_bytes=b'{"outcome":"UP","settled":true}',source_class="PLATFORM_TERMINAL_RESOLUTION")
        self.assertEqual(t1["label_authority"],"UNVERIFIED")
        self.assertFalse(t1["source_admissible"])
        self.assertEqual(t1["n_real_verified"],0)
        with self.assertRaises(IntegrityError):
            self.capture.record_t1(self.slot(),received_at_ms=620000,
                 raw_bytes=b"duplicate",source_class="PLATFORM_TERMINAL_RESOLUTION")

    def test_partial_ask_quote_not_monetizable(self):
        with self.assertRaises(CostError):
            hold_quote_net([{"price":"0.5","size":"1"}],"2",outcome=1,
                           fee_rate="0.07",exponent=1)

    def test_small_decimal_quantities_do_not_round_through_float(self):
        qty="0.123456789012345678901234"
        q=walk_asks([{"price":"0.5","size":qty}],qty)
        self.assertEqual(q["filled_shares_decimal"],qty)
        self.assertEqual(q["gross_cost_exact_decimal"],str(Decimal(qty)*Decimal("0.5")))
        hold=hold_quote_net([{"price":"0.5","size":qty}],qty,
                            outcome=1,fee_rate="0.07",exponent=1)
        # Audit PnL must be based on original Decimal shares, never float shares.
        self.assertEqual(hold["paper_net_usdc_decimal"],"0.05957")


if __name__=="__main__":unittest.main()
