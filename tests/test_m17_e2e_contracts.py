"""ORDER213 TDD tests: raw documentary CTF/Gamma + real custody cross-PR boundary."""
from __future__ import annotations
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from research.edge.oracle_aligned_net_ev.e2e_contracts import (
    classify_ctf_gamma_originals, bridge_p2_to_p1, OfflineCompatibilityEvidence, IntegrationError
)
from research.edge.oracle_aligned_net_ev.provider_adapters import (
    OriginalOfflineCustody, EvidenceError
)
from research.edge.oracle_aligned_net_ev.custody_store import IntegrityError
from research.edge.oracle_aligned_net_ev.book_costs import fee_usdc_decimal, CostError
from research.edge.oracle_aligned_net_ev.cost_identity import paper_buy
from research.edge.oracle_aligned_net_ev.quote_readiness import classify_buy, classify_pair

COND="0x"+"33"*32
ORACLE="0x"+"22"*20
CTF="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
BLOCK="0x"+"55"*32
TX="0x"+"66"*32

def wire(obj): return json.dumps(obj,separators=(',',':')).encode()

class OnchainDocumentaryTests(unittest.TestCase):
    def setUp(self):
        self.ctf={"chainId":137,"contract":CTF,"oracle":ORACLE,
          "conditionId":COND,"questionId":"0x"+"44"*32,
          "outcomeSlotCount":2,"payoutNumerators":[1,0],
          "blockNumber":95000000,"blockHash":BLOCK,"transactionHash":TX,
          "logIndex":0,"removed":False,"finalityConfirmations":128}
        self.gamma={"id":"fixture-market-1","chainId":137,"conditionId":COND,
          "outcomes":["Up","Down"],"clobTokenIds":["yes-token","no-token"],
          "winningOutcome":"Up"}
    def classify(self,**updates):
        c=copy.deepcopy(self.ctf);g=copy.deepcopy(self.gamma)
        c.update(updates.get("ctf",{}));g.update(updates.get("gamma",{}))
        return classify_ctf_gamma_originals(wire(c),wire(g),
            market_id="fixture-market-1",expected_oracle=ORACLE,
            expected_token_ids=("yes-token","no-token"))
    def assert_blocked(self,**updates):
        r=self.classify(**updates)
        self.assertEqual(r["status"],"LABEL_UNVERIFIED")
        self.assertFalse(r["source_admissible"])
        self.assertEqual(r["label_authority"],"UNVERIFIED")
    def test_valid_synthetic_identity_is_not_attestation(self):
        r=self.classify()
        self.assertEqual(r["status"],"DOCUMENTARY_PAYOUT_MATCH_UNVERIFIED")
        self.assertEqual(r["onchain_payout_label"],"Up")
        self.assertEqual(r["signed_original_twap_authority"],"NOT_VERIFIED")
        self.assertFalse(r["source_admissible"])
        self.assertFalse(r["eligible"])
        self.assertFalse(r["fill_proven"])
        self.assertEqual(r["ctf_original_sha256"],hashlib.sha256(wire(self.ctf)).hexdigest())
        self.assertEqual(r["gamma_original_sha256"],hashlib.sha256(wire(self.gamma)).hexdigest())
    def test_wrong_chain(self):self.assert_blocked(ctf={"chainId":1})
    def test_wrong_ctf(self):self.assert_blocked(ctf={"contract":"0x"+"aa"*20})
    def test_wrong_condition(self):self.assert_blocked(gamma={"conditionId":"0x"+"99"*32})
    def test_wrong_market(self):self.assert_blocked(gamma={"id":"another-market"})
    def test_wrong_oracle(self):self.assert_blocked(ctf={"oracle":"0x"+"aa"*20})
    def test_reversed_outcomes(self):self.assert_blocked(gamma={"outcomes":["Down","Up"]})
    def test_reversed_token_order(self):self.assert_blocked(gamma={"clobTokenIds":["no-token","yes-token"]})
    def test_wrong_payout_sum(self):self.assert_blocked(ctf={"payoutNumerators":[1,1]})
    def test_wrong_payout_length(self):self.assert_blocked(ctf={"payoutNumerators":[1]})
    def test_missing_block_hash(self):self.assert_blocked(ctf={"blockHash":None})
    def test_insufficient_finality(self):self.assert_blocked(ctf={"finalityConfirmations":2})
    def test_removed_reorg_log(self):self.assert_blocked(ctf={"removed":True})
    def test_missing_gamma_winner(self):self.assert_blocked(gamma={"winningOutcome":None})
    def test_fractional_log_index(self):self.assert_blocked(ctf={"logIndex":0.5})
    def test_ambiguous_duplicate_json_keys(self):
        raw=wire(self.ctf).replace(b'"chainId":137',b'"chainId":137,"chainId":1')
        r=classify_ctf_gamma_originals(raw,wire(self.gamma),market_id="fixture-market-1",expected_oracle=ORACLE)
        self.assertEqual(r["status"],"LABEL_UNVERIFIED")
    def test_custodied_offline_fixture_integrity_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=OfflineCompatibilityEvidence(Path(tmp))
            c,g=wire(self.ctf),wire(self.gamma)
            verdict=classify_ctf_gamma_originals(c,g,market_id="fixture-market-1",expected_oracle=ORACLE)
            entry=store.append(kind="CTF_GAMMA_FIXTURE",slot_key="fixture-only",now_ms=100,
                artifacts={"raw_frame":c,"market_metadata":g},
                attrs={"label_authority":"UNVERIFIED","source_admissible":False,
                       "fixture_verdict":verdict["status"]})
            store2=OfflineCompatibilityEvidence(Path(tmp))
            self.assertEqual(store2.read_bytes(entry["artifacts"]["raw_frame"]),c)
            self.assertEqual(store2.read_bytes(entry["artifacts"]["market_metadata"]),g)
            self.assertEqual(len(store2.verify()),1)
            ref=Path(tmp)/"blobs"/entry["artifacts"]["raw_frame"]["file"]
            ref.write_bytes(b"corruption")
            with self.assertRaises(IntegrityError):store2.verify()

class CrossParentIntegrationTests(unittest.TestCase):
    def test_p2_raw_preserved_as_p1_fixture_not_t0(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            p2=OriginalOfflineCustody(a);p1=OfflineCompatibilityEvidence(b)
            raw=wire({"id":"fixture-market-1","conditionId":"c1",
                       "outcomes":["Up","Down"],"clobTokenIds":["yes-token","no-token"]})
            e=p2.record(raw,source_class="GAMMA",parser_version="p2-fixture",
              received_wall_ms=1000,received_monotonic_ns=21,market_id="fixture-market-1",
              condition_id="c1",asset_id="yes-token",connection_id="fixture-connection")
            out=bridge_p2_to_p1(p2,p1,e,now_monotonic_ms=101)
            self.assertEqual(out["kind"],"P2_COMPAT_FIXTURE")
            self.assertEqual(out["artifacts"]["raw_frame"]["sha256"],hashlib.sha256(raw).hexdigest())
            self.assertEqual(p1.read_bytes(out["artifacts"]["raw_frame"]),raw)
            self.assertFalse(out["source_admissible"])
            self.assertFalse(out["fill_proven"])
            self.assertFalse(out["eligible"])
            self.assertFalse(any(x["kind"]=="T0_SLOT" for x in p1.verify()))
            with self.assertRaises(IntegrationError):
                bridge_p2_to_p1(p2,p1,e,now_monotonic_ms=102)
    def test_cross_module_fee_decimal_identical_for_exponent_one_only(self):
        for price in ("0.01","0.50","0.99"):
            p1=fee_usdc_decimal("5",price,fee_rate="0.03",exponent=1)
            p2=paper_buy(shares="5",price=price,rate="0.03",outcome=1,matched=True)
            self.assertEqual(str(p1),p2["conditional_fee_usdc_decimal"])
            self.assertFalse(p2["fill_proven"])
            self.assertFalse(p2["source_admissible"])
        with self.assertRaises(CostError):
            fee_usdc_decimal("5","0.5",fee_rate="0.03",exponent=2)
    def test_pair_no_fill_and_no_atomized_execution_claim(self):
        quote={"market":"c1","asset_id":"tYES","timestamp":999,"hash":"foo",
               "min_order_size":"5","tick_size":"0.01",
               "bids":[{"price":"0.48","size":"6"}],
               "asks":[{"price":"0.51","size":"6"}]}
        yes=classify_buy(quote,condition_id="c1",asset_id="tYES",candidate_shares="5",
             price_cap="0.55",observed_at_ms=1000,max_age_ms=500,original_sha256="a"*64)
        quote_no=dict(quote,asset_id="tNO",timestamp=997)
        no=classify_buy(quote_no,condition_id="c1",asset_id="tNO",candidate_shares="5",
             price_cap="0.55",observed_at_ms=1000,max_age_ms=500,original_sha256="b"*64)
        self.assertFalse(yes["fill_proven"])
        self.assertFalse(no["fill_proven"])
        paired=classify_pair(yes,no)
        self.assertEqual(paired["status"],"NON_ATOMIC_PAIR")
        self.assertFalse(paired["fill_proven"])
