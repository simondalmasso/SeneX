"""M17 oracle and CLOB contracts: synthetic fixtures only; no observed outcomes."""
import hashlib
import unittest

from research.edge.oracle_aligned_net_ev.oracle_custody import (
    CustodyError, resolution_side, validate_receipt_linkage,
)
from research.edge.oracle_aligned_net_ev.book_costs import (
    CostError, fee_usdc, walk_asks, hold_quote_net, paired_quote_upper_bound,
)


class OracleNetContractTests(unittest.TestCase):
    def fixtures(self):
        t0_raw = b"test-only-original-t0"
        t1_raw = b"test-only-original-t1"
        rule_raw = b"test-only-rule-5m-chainlink-twap"
        sha = lambda data: hashlib.sha256(data).hexdigest()
        t0 = dict(
            market_id="market1", condition_id="cond1", token_id_up="up1",
            token_id_down="down1", start_ms=300000, end_ms=600000,
            oracle_source_id="chainlink-btc-usd-twap-60s",
            original_market_rules_bytes_sha256=sha(rule_raw),
            raw_bytes_sha256=sha(t0_raw),
            received_at_ms=350000,
        )
        t1 = dict(
            market_id="market1", condition_id="cond1", token_id="up1",
            start_ms=300000, end_ms=600000,
            oracle_source_id="chainlink-btc-usd-twap-60s",
            original_market_rules_bytes_sha256=sha(rule_raw),
            raw_label_bytes_sha256=sha(t1_raw),
            label_observed_at_ms=700000,
            settlement_finality="final",
            source_class="PROVIDER_CHAINLINK_RELAY",
        )
        return t0, t1, t0_raw, t1_raw, rule_raw

    def test_market_rule_equality_is_up_not_down(self):
        rule = {"tie_winner": "UP", "source": "chainlink-btc-usd-twap-60s"}
        self.assertEqual(resolution_side(100, 100, rule), "UP")
        self.assertEqual(resolution_side(100, 99.99, rule), "DOWN")
        with self.assertRaises(CustodyError):
            resolution_side(100, 100, {"source": "binance"})

    def test_original_bytes_and_market_binding_only_not_oracle_signature(self):
        t0, t1, raw0, raw1, rule = self.fixtures()
        linked = validate_receipt_linkage(t0, t1, raw0, raw1, rule)
        self.assertEqual(linked["custody"], "MATCHED_BYTES_ONLY")
        self.assertEqual(linked["label_authority"], "UNVERIFIED")
        self.assertEqual(linked["n_real_verified"], 0)

    def test_tampered_t0_and_t1_source_mismatch_rejected(self):
        t0, t1, raw0, raw1, rule = self.fixtures()
        with self.assertRaises(CustodyError):
            validate_receipt_linkage(t0, t1, raw0 + b"x", raw1, rule)
        t1["oracle_source_id"] = "binance-btcusdt"
        with self.assertRaises(CustodyError):
            validate_receipt_linkage(t0, t1, raw0, raw1, rule)

    def test_mismatched_window_token_and_future_label_rejected(self):
        t0, t1, raw0, raw1, rule = self.fixtures()
        for key, value in (("end_ms", 900000), ("token_id", "other")):
            bad = dict(t1, **{key: value})
            with self.assertRaises(CustodyError):
                validate_receipt_linkage(t0, bad, raw0, raw1, rule)
        bad = dict(t1, label_observed_at_ms=500000)
        with self.assertRaises(CustodyError):
            validate_receipt_linkage(t0, bad, raw0, raw1, rule)

    def test_fee_exact_example_and_missing_market_rate_fails_closed(self):
        self.assertAlmostEqual(fee_usdc(100, .5, fee_rate=.07, exponent=1), 1.75)
        with self.assertRaises(CostError):
            fee_usdc(100, .5, fee_rate=None, exponent=1)
        with self.assertRaises(CostError):
            fee_usdc(100, .5, fee_rate=.07, exponent=2)

    def test_ask_depth_walk_never_uses_midpoint(self):
        levels = [{"price": .48, "size": 10}, {"price": .52, "size": 20}]
        quote = walk_asks(levels, 25)
        self.assertAlmostEqual(quote["gross_cost"], 10*.48 + 15*.52)
        self.assertAlmostEqual(quote["vwap_ask"], (10*.48+15*.52)/25)
        self.assertTrue(quote["full_depth"])
        partial = walk_asks(levels, 40)
        self.assertFalse(partial["full_depth"])
        self.assertEqual(partial["filled_shares"], 30)

    def test_paper_hold_pnl_costs_and_no_fill(self):
        levels = [{"price": .5, "size": 100}]
        paper = hold_quote_net(levels, 100, outcome=1, fee_rate=.07, exponent=1, slippage_usdc=2)
        self.assertAlmostEqual(paper["paper_net_usdc"], 100*(1-.5)-1.75-2)
        self.assertEqual(paper["evidence"], "PAPER_QUOTE_ONLY")
        self.assertEqual(hold_quote_net([], 100, outcome=1, fee_rate=.07, exponent=1)["paper_net_usdc"], 0)

    def test_pair_gross_discount_can_be_net_negative_without_atomic_fills(self):
        up = [{"price": .498, "size": 100}]
        down = [{"price": .498, "size": 100}]
        pair = paired_quote_upper_bound(
            up, down, 100, up_condition="same", down_condition="same",
            up_fee_rate=.07, down_fee_rate=.07, exponent=1, other_costs=0,
        )
        self.assertLess(pair["gross_pair_margin_usdc"], 1)
        self.assertLess(pair["paper_net_upper_bound_usdc"], 0)
        self.assertIsNone(pair["paper_net_lower_bound_usdc"])
        self.assertEqual(pair["status"], "QUOTE_ONLY_NONMONETIZABLE")
        with self.assertRaises(CostError):
            paired_quote_upper_bound(
                up, down, 100, up_condition="a", down_condition="b",
                up_fee_rate=.07, down_fee_rate=.07, exponent=1,
            )


if __name__ == "__main__":
    unittest.main()
