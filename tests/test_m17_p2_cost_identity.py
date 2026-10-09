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
