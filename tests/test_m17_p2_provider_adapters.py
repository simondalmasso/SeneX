import hashlib, json, tempfile, unittest
from pathlib import Path
from research.edge.oracle_aligned_net_ev.provider_adapters import EvidenceError, OriginalOfflineCustody

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=OriginalOfflineCustody(Path(self.temp.name))
    def raw(self, payload):
        return json.dumps(payload, separators=(',',':')).encode()
    def record(self, payload, kind, **kwargs):
        return self.store.record(self.raw(payload), source_class=kind, parser_version='p2-v1',
            received_wall_ms=2000, received_monotonic_ns=1100, market_id='m1',
            condition_id='c1', asset_id='tYES', connection_id='conn1', **kwargs)
    def test_raw_bytes_unchanged_reopen_and_tamper(self):
        msg={'market':'c1','asset_id':'tYES','timestamp':1999,'hash':'abc',
            'min_order_size':'5','tick_size':'0.01','bids':[{'price':'0.49','size':'6'}],
            'asks':[{'price':'0.51','size':'6'}]}
        raw=self.raw(msg)
        r=self.record(msg,'CLOB_REST')
        self.assertEqual(r['original_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(r['source_admissible'],False)
        self.assertEqual(Path(self.temp.name,r['original_attachment']).read_bytes(),raw)
        self.assertEqual(len(self.store.reopen()),1)
        path=Path(self.temp.name,r['original_attachment']);path.write_bytes(b'changed')
        with self.assertRaises(EvidenceError): self.store.reopen()
    def test_bad_raw_price_grid_is_consistent_evidence_error(self):
        bad={'market':'c1','asset_id':'tYES','timestamp':1999,'hash':'h',
             'min_order_size':'5','tick_size':'0.01',
             'bids':[{'price':'0.49','size':'6'}],
             'asks':[{'price':'0.505','size':'6'}]}
        with self.assertRaises(EvidenceError):self.record(bad,'CLOB_REST')

    def test_wrong_clob_identity_rejected(self):
        with self.assertRaises(EvidenceError):
            self.record({'market':'OTHER','asset_id':'tYES','timestamp':1999},'CLOB_REST')
    def test_sdk_subscribe_does_not_become_forward_update(self):
        r=self.record({'topic':'prices.crypto.twap','type':'subscribe','timestamp':1999,
              'payload':{'symbol':'btcusd','windowSeconds':60,'data':[]}},'SDK_NORMALIZED')
        self.assertEqual(r['status'],'SNAPSHOT_NOT_FORWARD')
        self.assertEqual(r['source_origin'],'SDK_DERIVED_NOT_ORIGINAL_WIRE')
    def test_polybolt_raw_twap_exact_decimal_and_gap(self):
        snapshot={'v':1,'channel':'price.crypto.twap','seq':10,'ts':1998,'snapshot':True,
            'payload':{'symbol':'btcusd','window_seconds':60,'full_accuracy_value':'78200.123456789123'}}
        r=self.record(snapshot,'POLYBOLT_RAW')
        self.assertEqual(r['status'],'SNAPSHOT_NOT_FORWARD')
        update=dict(snapshot,seq=12,snapshot=False)
        with self.assertRaises(EvidenceError): self.record(update,'POLYBOLT_RAW')
        update['seq']=11
        r2=self.record(update,'POLYBOLT_RAW')
        self.assertEqual(r2['normalized']['value_decimal'],'78200.123456789123')
        self.assertEqual(r2['status'],'OFFLINE_UPDATE_UNVERIFIED')
    def test_polybolt_spot_not_twap_and_no_double_scaling(self):
        base={'v':1,'channel':'price.crypto','seq':1,'ts':1998,'snapshot':False,
            'payload':{'symbol':'btcusd','window_seconds':60,'full_accuracy_value':'78200'}}
        with self.assertRaises(EvidenceError):self.record(base,'POLYBOLT_RAW')
        legacy={'topic':'crypto_prices_twap_sixty','type':'update','timestamp':1999,
            'payload':{'symbol':'btc/usd','window_s':60,'full_accuracy_value':'78200123456789000000000','timestamp':1999}}
        r=self.record(legacy,'RTDS_LEGACY')
        self.assertEqual(r['normalized']['value_decimal'],'78200.123456789000000000')
    def test_bad_timestamps_and_declared_authority_rejected(self):
        book={'market':'c1','asset_id':'tYES','timestamp':1999.8,'hash':'h',
            'min_order_size':'5','tick_size':'0.01','bids':[{'price':'0.4','size':'5'}],
            'asks':[{'price':'0.5','size':'5'}]}
        with self.assertRaises(EvidenceError):self.record(book,'CLOB_REST')
        book['timestamp']=1999
        with self.assertRaises(EvidenceError):self.record(book,'CLOB_REST', source_admissible=True)
    def test_gamma_provider_identity_not_synthetic_rule(self):
        gamma={'id':'m1','conditionId':'c1','clobTokenIds':'["tYES","tNO"]',
            'outcomes':'["Yes","No"]'}
        rec=self.record(gamma,'GAMMA')
        self.assertEqual(rec['normalized']['token_ids'],['tYES','tNO'])
        self.assertIsNone(rec['normalized']['settlement_rule_verified'])
        gamma['conditionId']='OTHER'
        with self.assertRaises(EvidenceError): self.record(gamma,'GAMMA')

class CrashCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
    def test_orphan_refuses_reopen_and_no_auto_cleanup(self):
        (self.path/('raw_000001_'+'0'*64+'.bin')).write_bytes(b'orphan')
        with self.assertRaises(EvidenceError):OriginalOfflineCustody(self.path)
        self.assertEqual(len(list(self.path.glob('*.bin'))),1)
    def test_corrupt_or_torn_journal_tail_rejected(self):
        (self.path/'journal.jsonl').write_bytes(b'{"partial":true}')
        with self.assertRaises(EvidenceError):OriginalOfflineCustody(self.path)
    def test_new_connection_requires_new_snapshot(self):
        s=OriginalOfflineCustody(self.path)
        payload={'v':1,'channel':'price.crypto.twap','seq':1,'ts':1998,
                 'snapshot':False,'payload':{'symbol':'btcusd','window_seconds':60,
                                          'full_accuracy_value':'78200.1'}}
        with self.assertRaises(EvidenceError):s.record(json.dumps(payload).encode(),
             source_class='POLYBOLT_RAW',parser_version='p2-v1',received_wall_ms=2000,
             received_monotonic_ns=1,market_id='m',condition_id='c',asset_id='t',
             connection_id='different-connection')
        self.assertEqual(s.reopen(),[])
