from decimal import Decimal
import unittest
from research.edge.oracle_aligned_net_ev.cost_identity import paper_buy, CostError

class CostTests(unittest.TestCase):
    def cost(self,**kw):
        return paper_buy(shares=kw.get('shares','100'),price=kw.get('p','0.5'),
          rate=kw.get('rate','0.07'),outcome=1,matched=kw.get('matched',False),
          slippage=kw.get('slippage','0'),rebate=kw.get('rebate',None))
    def test_premium_denominators_exact(self):
        for p,ratio in [('0.01','0.0693'),('0.5','0.035'),('0.99','0.0007')]:
            r=self.cost(p=p)
            self.assertEqual(Decimal(r['fee_to_premium_decimal']),Decimal(ratio))
            self.assertFalse(r['fill_proven'])
            self.assertNotIn('realized_pnl',r)
    def test_unmatched_charges_no_fee_or_slippage(self):
        r=self.cost(p='0.5',matched=False,slippage='12')
        self.assertEqual(r['charged_fee_usdc_decimal'],'0')
        self.assertEqual(r['charged_slippage_usdc_decimal'],'0')
        self.assertEqual(r['status'],'QUOTE_ONLY_NONMONETIZABLE')
    def test_matching_hypothesis_remains_unverified(self):
        r=self.cost(matched=True)
        self.assertEqual(r['status'],'HYPOTHETICAL_MATCH_NOT_VERIFIED')
        self.assertEqual(r['conditional_fee_usdc_decimal'],'1.75000')
        self.assertNotIn('realized_pnl',r)
    def test_unknown_fee_and_fictitious_rebate_refused(self):
        with self.assertRaises(CostError):self.cost(rate=None)
        with self.assertRaises(CostError):self.cost(rebate='0.20')
    def test_nonfinite_quantity_and_outcome(self):
        with self.assertRaises(CostError):self.cost(shares='NaN')
        with self.assertRaises(CostError):paper_buy(shares='5',price='0.5',rate='0.07',outcome=2)

    def test_m17_market_fee_vintage_fail_closed(self):
        """A fixture fee curve is not the historic match-time market fee."""
        import hashlib
        import json
        from research.edge.oracle_aligned_net_ev import cost_identity
        gate=getattr(cost_identity,'paper_buy_with_market_vintage',None)
        self.assertTrue(callable(gate),'missing market-specific fee vintage boundary')
        market={'id':'synthetic-btc5m','version':'v1','oracleWindowSeconds':60,
                'feeSchedule':{'rate':'0.07','exponent':1,'takerOnly':True,
                               'feesEnabled':True,'effectiveFromMs':1000,'effectiveToMs':2000}}
        def assess(doc=market,**kw):
            raw=None if doc is None else json.dumps(doc,sort_keys=True,separators=(',',':')).encode('utf8')
            args={'original_market_bytes':raw,
                  'expected_market_sha256':hashlib.sha256(raw).hexdigest() if raw else None,
                  'market_id':'synthetic-btc5m','source_received_at_ms':1490,
                  't0_ms':1500,'assumed_match_at_ms':1501,
                  'expected_oracle_window_seconds':60,
                  'shares':'100','price':'0.5','rebate':None,'trade_id':None}
            args.update(kw)
            return gate(**args)
        good=assess()
        self.assertEqual(good['fee_vintage_status'],'CONDITIONAL_DOCUMENTARY_FIXTURE_ONLY')
        self.assertEqual(good['net_edge_status'],'NET_EDGE_NOT_COMPUTABLE')
        self.assertEqual(good['conditional_fee_usdc_decimal'],'1.75000')
        self.assertEqual(good['conditional_fee_per_share_decimal'],'0.01750')
        self.assertEqual(good['conditional_entry_cost_per_share_decimal'],'0.51750')
        self.assertEqual(good['conditional_break_even_probability_decimal'],'0.51750')
        self.assertFalse(good['source_admissible'])
        self.assertFalse(good['fill_proven'])
        self.assertEqual(good['rebate_credit_decimal'],'0')
        self.assertEqual(good['realized_pnl_status'],'NOT_COMPUTABLE')
        cases=[
            {'doc':None},
            {'doc':dict(market,feeSchedule=None)},
            {'doc':dict(market,feeSchedule=dict(market['feeSchedule'],exponent=2))},
            {'doc':dict(market,feeSchedule=dict(market['feeSchedule'],feesEnabled=None))},
            {'doc':dict(market,feeSchedule=dict(market['feeSchedule'],takerOnly=False))},
            {'kw':{'expected_market_sha256':'f'*64}},
            {'kw':{'source_received_at_ms':1502}},
            {'kw':{'assumed_match_at_ms':2000}},
            {'kw':{'rebate':'0.20'}},
            {'kw':{'trade_id':'unverified-matched-trade'}},
            {'doc':dict(market,oracleWindowSeconds=30)},
            {'doc':dict(market,id='wrong-market')},
        ]
        for case in cases:
            with self.subTest(case=case):
                result=assess(case.get('doc',market),**case.get('kw',{}))
                self.assertEqual(result['net_edge_status'],'NET_EDGE_NOT_COMPUTABLE')
                self.assertNotEqual(result['fee_vintage_status'],'CONDITIONAL_DOCUMENTARY_FIXTURE_ONLY')
                self.assertEqual(result['rebate_credit_decimal'],'0')
                self.assertFalse(result['source_admissible'])
                self.assertFalse(result['fill_proven'])
                self.assertEqual(result['realized_pnl_status'],'NOT_COMPUTABLE')
