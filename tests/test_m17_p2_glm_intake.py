import csv, io, unittest
from research.edge.oracle_aligned_net_ev.glm_intake import IntakeError, parse_glm_csv, derived_fixed_csv, evaluate_glm_claims

HEAD='family,id,mechanism,source,paper,regime,variable,falsification,leakage,priority,state,trial_id'
ROW='PDF,M1,book,paper-a,twap60,delta,test,no,high,EXPLORATORY_ONLY,trial-1'
class GLMTests(unittest.TestCase):
    def test_original_malformed_csv_fails_without_shift(self):
        raw=(HEAD+'\n'+ROW+'\n').encode()
        with self.assertRaises(IntakeError):parse_glm_csv(raw)
    def test_derived_correction_keeps_original_sha_and_family(self):
        raw=(HEAD+'\n'+ROW+'\n'+ROW.replace('PDF,M1','R9,M1')+'\n').encode()
        res=derived_fixed_csv(raw,missing_column='source',insert_value='UNVERIFIED_SOURCE')
        self.assertNotEqual(raw,res['derived_bytes'])
        self.assertEqual(res['original_bytes'],raw)
        rec=list(csv.DictReader(io.StringIO(res['derived_bytes'].decode())))
        self.assertEqual([x['family'] for x in rec],['PDF','R9'])
        self.assertEqual(rec[0]['source'],'UNVERIFIED_SOURCE')
        self.assertFalse(res['scientific_authorized'])
    def test_glm_r4_and_order099_history_not_synthetic_proof(self):
        report=evaluate_glm_claims({'glm_r4_synthetic_20pct':True})
        self.assertEqual(report['historical_order099'],'REFUTED_ISSUE_139')
        self.assertEqual(report['glm_r4'],'UNREPRODUCED')
        self.assertEqual(report['glm_hypotheses'],'EXPLORATORY_ONLY')
        self.assertEqual(report['twap_delta_units'],'PRICE_NOT_REVERSAL_PROBABILITY')
