import unittest
from research.edge.oracle_aligned_net_ev.quote_readiness import QuoteError, classify_buy, classify_pair

class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.book={'market':'c1','asset_id':'tYES','timestamp':900,'hash':'hash1',
          'min_order_size':'5','tick_size':'0.01','asks':[{'price':'0.51','size':'6'}],
          'bids':[{'price':'0.49','size':'8'}]}
    def quote(self,book=None,**kwargs):
        return classify_buy(book or self.book,condition_id='c1',asset_id='tYES',
             candidate_shares=kwargs.get('shares','5'),price_cap=kwargs.get('cap','0.52'),
             observed_at_ms=kwargs.get('now',1000),max_age_ms=100,
             original_sha256='a'*64)
    def test_one_share_below_market_min_abstains(self):
        self.assertEqual(self.quote(shares='1')['status'],'NO_BOOK')
    def test_offtick_and_partial_depth(self):
        b=dict(self.book,asks=[{'price':'0.505','size':'6'}])
        self.assertEqual(self.quote(b)['status'],'NO_BOOK')
        b=dict(self.book,asks=[{'price':'0.51','size':'2'}])
        self.assertEqual(self.quote(b)['status'],'NO_BOOK')
    def test_observed_quote_never_fill_or_probability(self):
        r=self.quote()
        self.assertEqual(r['status'],'QUOTE_ONLY_NONMONETIZABLE')
        self.assertFalse(r['fill_proven'])
        self.assertNotIn('fill_probability',r)
        self.assertNotIn('realized_pnl',r)
        self.assertEqual(r['conditional_premium_decimal'],'2.55')
    def test_stale_wrong_market_missing_source(self):
        self.assertEqual(self.quote(now=1002)['status'],'STALE_BOOK')
        self.assertEqual(self.quote(dict(self.book,market='OTHER'))['status'],'NO_BOOK')
        self.assertEqual(self.quote(dict(self.book,hash=''))['status'],'NO_BOOK')
    def test_pair_with_missing_leg_is_no_book_not_nonatomic_pair(self):
        present=self.quote()
        absent=dict(present, asset_id='tNO', status='NO_BOOK', quote_available=False)
        self.assertEqual(classify_pair(present,absent)['status'],'NO_BOOK')

    def test_sequential_pair_never_atomic(self):
        a=self.quote();b=dict(a,asset_id='tNO',observed_at_ms=1002)
        self.assertEqual(classify_pair(a,b)['status'],'NON_ATOMIC_PAIR')

class SellAndBoundaries(unittest.TestCase):
    def setUp(self):
        self.book={'market':'c1','asset_id':'tYES','timestamp':900,'hash':'b',
            'min_order_size':'5','tick_size':'0.01',
            'bids':[{'price':'0.49','size':'6'}],
            'asks':[{'price':'0.51','size':'6'}]}
    def test_naked_sale_refused_then_quote_only(self):
        from research.edge.oracle_aligned_net_ev.quote_readiness import classify_sell
        args=dict(condition_id='c1',asset_id='tYES',candidate_shares='5',
            price_floor='0.48',observed_at_ms=1000,max_age_ms=100,original_sha256='f'*64)
        r=classify_sell(self.book,verified_inventory_shares='0',**args)
        self.assertEqual(r['status'],'ABSTAIN_NO_INVENTORY')
        good=classify_sell(self.book,verified_inventory_shares='5',**args)
        self.assertEqual(good['status'],'QUOTE_ONLY_NONMONETIZABLE')
        self.assertFalse(good['fill_proven'])
        self.assertEqual(good['conditional_sale_proceeds_decimal'],'2.45')
    def test_crossed_and_nonfinite_depth_abstain(self):
        book=dict(self.book,bids=[{'price':'0.52','size':'8'}])
        args=dict(condition_id='c1',asset_id='tYES',candidate_shares='5',price_cap='0.52',
             observed_at_ms=1000,max_age_ms=100,original_sha256='b'*64)
        self.assertEqual(classify_buy(book,**args)['status'],'NO_BOOK')
        book=dict(self.book,asks=[{'price':'0.51','size':'Infinity'}])
        self.assertEqual(classify_buy(book,**args)['status'],'NO_BOOK')
