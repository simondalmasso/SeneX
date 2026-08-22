from __future__ import annotations
import asyncio, hashlib, inspect, unittest
from pathlib import Path
from unittest import mock
from backend import supabase_client
from backend.authority_snapshot import _canonical_rows

ROOT=Path(__file__).resolve().parents[2]
APPROVED_BRIDGE_SHA256={
    "senecio_polymarket/oracle_runtime/predict_only.py": "baed36d907354970d7a58c745d0b719300bf35a3b272d742aef59c55abec3660",
    "senecio_polymarket/oracle_runtime/institutional_core_real.py": "12b810f54c00f170b2ef577b6ae07fbab1374f39f0e14503c801045141b45557",
}

class R8Acceptance(unittest.TestCase):
    def test_decision_bridge_is_exact_approved_4b107_surface(self):
        for rel, expected in APPROVED_BRIDGE_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(), expected)

    def test_authority_query_projects_only_proof_score_fields(self):
        select=supabase_client.AUTHORITY_HISTORY_SELECT
        self.assertIn("origin_price_v1:audit->origin_price_v1", select)
        self.assertIn("outcomes_dual:audit->outcomes_dual", select)
        self.assertNotIn("pipeline", select)
        self.assertNotIn("external_markets", select)
        raw={"id":1,"ts":"2026-01-01T00:00:00Z","symbol":"BTCUSDT","prediction":"LONG","confidence":0.6,"price_now":1,"outcome":"WIN","exchange_used":"okx","origin_price_v1":{"version":"origin-price-v1"},"outcomes_dual":{"outcome_1h":"WIN"},"irrelevant":"x"}
        out=supabase_client._authority_row_from_projection(raw)
        self.assertNotIn("origin_price_v1",out); self.assertNotIn("outcomes_dual",out)
        self.assertEqual(set(out["audit"]),{"origin_price_v1","outcomes_dual"})

    def test_canonical_rows_do_not_json_roundtrip_whole_cohort(self):
        source=inspect.getsource(_canonical_rows)
        self.assertNotIn("json.loads", source)
        self.assertNotIn("_canonical_json(row)", source)
        rows=[{"ts":"2","id":"2","audit":{"x":1}},{"ts":"1","id":"1","audit":{"x":2}}]
        out=_canonical_rows(rows)
        self.assertEqual([x["id"] for x in out],["1","2"]); self.assertIsNot(out[0],rows[1])

    def test_dashboard_readiness_uses_existing_context_poll_without_new_timer(self):
        js=(ROOT/'senecio_polymarket/frontend/app.js').read_text()
        self.assertIn("payload.readiness",js)
        self.assertIn("READINESS_",js)
        self.assertNotIn("getJSON('/readyz",js)
        self.assertEqual(js.count("setInterval(refreshContext, 2000)"),1)

    def test_market_context_exposes_same_readiness_helper(self):
        src=(ROOT/'senecio_polymarket/backend/main_real.py').read_text()
        self.assertIn('"readiness": readiness',src)
        self.assertIn('readiness = _readiness_payload(snap, refresh)',src)
        self.assertIn('payload = _readiness_payload(snap, refresh)',src)

    def test_readiness_explicit_not_ready_fails_closed_but_missing_is_compatible(self):
        js=(ROOT/'senecio_polymarket/frontend/app.js').read_text()
        self.assertIn("if (readiness.status === 'not_ready')", js)
        self.assertIn("domainFailure('context', new Error('READINESS_NOT_READY'))", js)
        self.assertNotIn("READINESS_${readiness.status || 'UNKNOWN'}", js)

if __name__=='__main__': unittest.main()
