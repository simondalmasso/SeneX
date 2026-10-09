"""AUD P1 regression: schema-valid synthetic receipts; no real-market claims."""
import hashlib
import json
import math
import unittest
from pathlib import Path

from research.edge.oracle_aligned_net_ev.oracle_custody import (
    CustodyError, canonical_receipt_bytes, sha256_bytes,
    validate_receipt_linkage as _checked_linkage,
)
from research.edge.oracle_aligned_net_ev.book_costs import (
    CostError, fee_usdc_decimal, hold_quote_net, walk_asks,
)
from research.edge.oracle_aligned_net_ev.twap_source import (
    SourceError, parse_twap_frame, classify_twap_transport,
)

ROOT = Path(__file__).resolve().parents[1]
LANE = ROOT / "research" / "edge" / "oracle_aligned_net_ev"


def validate_with_schema(instance, file):
    schema = json.loads((LANE / file).read_text(encoding="utf-8"))
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        # Dependency-free strict validation of the JSON Schema keywords used in these two artifacts.
        for field in schema["required"]:
            assert field in instance, "missing " + field
        assert set(instance).issubset(schema["properties"]), "additionalProperties:false"
        for key, value in instance.items():
            spec = schema["properties"][key]
            allowed = spec.get("type")
            if allowed is not None:
                choices = allowed if isinstance(allowed, list) else [allowed]
                def match(typ):
                    return (typ == "null" and value is None or
                            typ == "string" and type(value) is str or
                            typ == "integer" and type(value) is int or
                            typ == "number" and type(value) in (int, float) and math.isfinite(value) or
                            typ == "object" and type(value) is dict or
                            typ == "array" and type(value) is list)
                assert any(match(t) for t in choices), key + ": invalid type"
            if "const" in spec:
                assert value == spec["const"], key + ": const mismatch"
            if "enum" in spec:
                assert value in spec["enum"], key + ": enum mismatch"
            if "minLength" in spec and value is not None:
                assert len(value) >= spec["minLength"], key + ": empty"
            if "pattern" in spec and value is not None:
                import re
                assert re.fullmatch(spec["pattern"], value), key + ": sha"
        return
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(instance)


def make_receipts():
    rule_raw = b"synthetic market-specific BTC 5m Chainlink 60s TWAP terms v1"
    t0_raw = b"synthetic original pre-outcome opportunity"
    t1_raw = b"synthetic terminal platform observation (NOT signed oracle)"
    sha = sha256_bytes
    rule_sha = sha(rule_raw)
    t0 = {
        "experiment_id":"M17_TEST", "cohort_id":"TEST_ONLY", "hypothesis_id":"H1",
        "market_slug":"btc-updown-5m-300", "event_id":"event-1",
        "market_id":"market-1", "condition_id":"condition-1",
        "token_id_yes":"UP-1", "token_id_no":"DOWN-1",
        "rule_version_sha256":rule_sha,
        "original_market_rules_bytes_sha256":rule_sha,
        "oracle_source_id":"btc-5m-twap-60",
        "start_ms":300000, "end_ms":600000,
        "oracle_boundary_rule":"UP_ON_EQUAL",
        "time_of_observation_ms":350000, "received_at_ms":350200,
        "clock_skew_bound_ms":500,
        "prediction_id":"prediction-test", "frozen_senex_score":0.3,
        "score_provenance":"RAW_CONVICTION",
        "venue":"POLYMARKET", "symbol":"BTC/USD", "horizon_s":300,
        "side_candidate":"UP",
        "best_bid":0.48, "best_ask":0.51, "bid_qty":100, "ask_qty":100,
        "top_N_depth":{"UP":{"asks":[{"price":"0.51","size":"100"}]}},
        "book_seq_or_version":"seq-1", "book_snapshot_sha256":sha(b"synthetic book"),
        "real_quote_age_ms":150, "market_fee_parameters":{"feeType":"crypto_fees_v2",
            "rate":"0.07", "exponent":1, "rounding":"ROUND_HALF_UP", "precision_decimals":5},
        "fee_parameters_sha256":sha(b"synthetic fee"),
        "cost_model_sha256":sha(b"synthetic cost model"),
        "validity_flags":[], "raw_bytes_sha256":sha(t0_raw),
    }
    t1 = {
        "market_id":"market-1", "condition_id":"condition-1",
        "token_id":"UP-1",
        "rule_version_sha256":rule_sha,
        "original_market_rules_bytes_sha256":rule_sha,
        "start_ms":300000,"end_ms":600000,
        "exact_oracle_source":"btc-5m-twap-60",
        "source_class":"PROVIDER_CHAINLINK_RELAY",
        "source_attestation_or_public_resolution_reference":None,
        "settled_start_value":100.0, "settled_end_value":100.0,
        "tie_handling":"UP_ON_EQUAL",
        "authoritative_event_outcome":"UP", "settlement_finality":"final",
        "label_observed_at_ms":700000, "raw_label_bytes_sha256":sha(t1_raw),
        "T0_receipt_sha256":sha(canonical_receipt_bytes(t0)),
        "join_provenance_and_uniqueness":{"join_key":"market-1:UP-1:300000"},
        "independent_signature_verification":"NOT_VERIFIED",
    }
    return t0,t1,t0_raw,t1_raw,rule_raw


def validate_receipt_linkage(t0,t1,raw0,raw1,rule):
    return _checked_linkage(t0,t1,raw0,raw1,rule,t0_receipt_raw=canonical_receipt_bytes(t0))


class M17AuditP1Tests(unittest.TestCase):
    def test_schema_valid_fixture_round_trip_without_authority_upgrade(self):
        t0,t1,raw0,raw1,rule=make_receipts()
        validate_with_schema(t0, "T0_RECEIPT_SCHEMA_V1.json")
        validate_with_schema(t1, "T1_LABEL_SCHEMA_V1.json")
        result=validate_receipt_linkage(t0,t1,raw0,raw1,rule)
        self.assertEqual(result["custody"],"MATCHED_BYTES_ONLY")
        self.assertEqual(result["label_authority"],"UNVERIFIED")
        self.assertEqual(result["n_real_verified"],0)

    def test_t0_receipt_and_rule_version_tamper_rejected(self):
        t0,t1,raw0,raw1,rule=make_receipts()
        for key,value in [
            ("T0_receipt_sha256","f"*64),
            ("rule_version_sha256","f"*64),
        ]:
            bad=dict(t1,**{key:value})
            with self.subTest(key=key),self.assertRaises(CustodyError):
                validate_receipt_linkage(t0,bad,raw0,raw1,rule)
        bad=dict(t0,side_candidate="DOWN")
        with self.assertRaises(CustodyError):
            validate_receipt_linkage(bad,t1,raw0,raw1,rule)
        with self.assertRaises(CustodyError):
            validate_receipt_linkage(t0,t1,raw0,raw1,rule+b"changed")

    def test_original_t0_receipt_required_and_tampered_bytes_rejected(self):
        t0,t1,raw0,raw1,rule=make_receipts()
        with self.assertRaises(CustodyError):
            _checked_linkage(t0,t1,raw0,raw1,rule,t0_receipt_raw=b"")
        with self.assertRaises(CustodyError):
            _checked_linkage(t0,t1,raw0,raw1,rule,t0_receipt_raw=canonical_receipt_bytes(t0)+b" ")
        self.assertEqual(
            _checked_linkage(t0,t1,raw0,raw1,rule,t0_receipt_raw=canonical_receipt_bytes(t0))["custody"],
            "MATCHED_BYTES_ONLY",
        )

    def test_source_and_window_mismatch_rejected(self):
        t0,t1,raw0,raw1,rule=make_receipts()
        for key,val in [("exact_oracle_source","binance"),("token_id","other"),
                        ("end_ms",900000),("tie_handling","DOWN_ON_EQUAL")]:
            with self.subTest(key=key),self.assertRaises(CustodyError):
                validate_receipt_linkage(t0,dict(t1,**{key:val}),raw0,raw1,rule)

    def test_no_signal_no_book_preserves_denominator(self):
        t0,t1,raw0,raw1,rule=make_receipts()
        t0.update(prediction_id=None,frozen_senex_score=None,score_provenance="NO_T0_SENEX_SIGNAL",
                  best_bid=None,best_ask=None,bid_qty=None,ask_qty=None,
                  book_seq_or_version=None,book_snapshot_sha256=None,
                  fee_parameters_sha256=None,
                  top_N_depth={},real_quote_age_ms=None,
                  market_fee_parameters={},
                  side_candidate="ABSTAIN",
                  validity_flags=["NO_T0_SENEX_SIGNAL","NO_BOOK","UNVERIFIED_FEE"])
        validate_with_schema(t0,"T0_RECEIPT_SCHEMA_V1.json")
        t1["T0_receipt_sha256"]=sha256_bytes(canonical_receipt_bytes(t0))
        self.assertEqual(validate_receipt_linkage(t0,t1,raw0,raw1,rule)["label_authority"],"UNVERIFIED")
        from research.edge.oracle_aligned_net_ev.oracle_custody import classify_opportunity
        state=classify_opportunity(t0)
        self.assertEqual(state["opportunity_count"],1)
        self.assertTrue(state["excluded"])
        self.assertIn("NO_T0_SENEX_SIGNAL",state["exclusion_reasons"])
        self.assertIn("NO_BOOK",state["exclusion_reasons"])
        self.assertEqual(state["portfolio_pnl_when_abstained"],"0")

    def test_money_precision_is_decimal_and_rounds_tiny_amounts(self):
        self.assertEqual(str(fee_usdc_decimal("100","0.5",fee_rate="0.07",exponent=1)),"1.75000")
        self.assertEqual(str(fee_usdc_decimal("0.0001","0.5",fee_rate="0.07",exponent=1)),"0.00000")
        self.assertEqual(str(fee_usdc_decimal("0.001","0.5",fee_rate="0.07",exponent=1)),"0.00002")
        with self.assertRaises(CostError):
            fee_usdc_decimal("1","0.5",fee_rate=None,exponent=1)
        with self.assertRaises(CostError):
            fee_usdc_decimal("-1","0.5",fee_rate="0.07",exponent=1)

    def test_quote_exposes_auditable_decimal_strings(self):
        quote=walk_asks([{"price":"0.51","size":"3"},{"price":"0.52","size":"2"}],"4")
        self.assertEqual(quote["gross_cost_decimal"],"2.05000")
        paper=hold_quote_net([{"price":"0.5","size":"100"}],100,
            outcome=0,fee_rate="0.07",exponent=1)
        self.assertEqual(paper["fee_usdc_decimal"],"1.75000")
        self.assertEqual(paper["paper_net_usdc_decimal"],"-51.75000")

    def test_modern_twap_requires_correct_topic_window_symbol_and_decimal(self):
        raw=json.dumps({"topic":"prices.crypto.twap","type":"update",
            "timestamp":400100,"seq":10,
            "payload":{"symbol":"btcusd","windowSeconds":60,
                       "timestamp":400000,"value":"70000.000000000000000001"}}).encode()
        out=parse_twap_frame(raw,received_at_ms=400150,transport="SECURE_REALTIME",
                             max_age_ms=1000,previous_seq=9)
        self.assertEqual(out["value_decimal"],"70000.000000000000000001")
        self.assertEqual(out["source_timestamp_ms"],400000)
        self.assertEqual(out["raw_bytes_sha256"],hashlib.sha256(raw).hexdigest())
        self.assertFalse(out["gap_detected"])
        for key,val in [("topic","crypto_prices_chainlink"),
                        ("timestamp",400300)]:
            j=json.loads(raw)
            j[key]=val
            with self.subTest(key=key),self.assertRaises(SourceError):
                parse_twap_frame(json.dumps(j).encode(),received_at_ms=400150,
                                 transport="SECURE_REALTIME",max_age_ms=1000)
        j=json.loads(raw);j["payload"]["windowSeconds"]=30
        with self.assertRaises(SourceError):
            parse_twap_frame(json.dumps(j).encode(),received_at_ms=400150,
                             transport="SECURE_REALTIME",max_age_ms=1000)

    def test_legacy_e18_not_generic_chainlink_and_gap_check(self):
        raw=json.dumps({"topic":"crypto_prices_twap_sixty","type":"update","seq":5,
            "payload":{"symbol":"btc/usd","timestamp":500000,
                       "full_accuracy_value":"70000000000000000000000","window_s":60}}).encode()
        out=parse_twap_frame(raw,received_at_ms=500100,
                             transport="LEGACY_RTDS",max_age_ms=500,previous_seq=3)
        self.assertEqual(out["value_decimal"],"70000")
        self.assertTrue(out["gap_detected"])
        j=json.loads(raw);j["topic"]="crypto_prices_chainlink"
        with self.assertRaises(SourceError):
            parse_twap_frame(json.dumps(j).encode(),received_at_ms=500100,
                             transport="LEGACY_RTDS",max_age_ms=500)
        j=json.loads(raw);j["payload"]["window_s"]=30
        with self.assertRaises(SourceError):
            parse_twap_frame(json.dumps(j).encode(),received_at_ms=500100,
                             transport="LEGACY_RTDS",max_age_ms=500)
        j=json.loads(raw);j["payload"]["timestamp"]=490000
        with self.assertRaises(SourceError):
            parse_twap_frame(json.dumps(j).encode(),received_at_ms=500100,
                             transport="LEGACY_RTDS",max_age_ms=500)

    def test_unverified_transport_is_never_scientific_label(self):
        self.assertEqual(classify_twap_transport("SECURE_REALTIME")["requires_auth"],True)
        self.assertEqual(classify_twap_transport("LEGACY_RTDS")["independent_chainlink_attestation"],False)
        with self.assertRaises(SourceError):
            classify_twap_transport("crypto_prices_chainlink")


if __name__=="__main__":
    unittest.main()
