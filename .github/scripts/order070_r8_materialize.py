from __future__ import annotations
import hashlib, os, re, subprocess
from pathlib import Path

ROOT=Path(os.environ.get('CANDIDATE_DIR','candidate')).resolve()
BASE='4b107bfb427cb85ea84850ffd9ddd5d7a4231d94'
TREE='5d1d9ec806b7d0e02031726565f08ef75d5a9340'

def run(*a,check=True):
    p=subprocess.run(list(a),cwd=ROOT,text=True,capture_output=True)
    if check and p.returncode: raise RuntimeError(f"CMD_FAIL {a}:\n{p.stdout}\n{p.stderr}")
    return p

def text(path): return (ROOT/path).read_text()
def write(path,s): (ROOT/path).write_text(s)
def replace_once(s,old,new,label):
    if s.count(old)!=1: raise RuntimeError(f'{label}: expected 1 occurrence, got {s.count(old)}')
    return s.replace(old,new,1)

if run('git','rev-parse','HEAD').stdout.strip()!=BASE: raise RuntimeError('BASE_SHA_DRIFT')
if run('git','rev-parse','HEAD^{tree}').stdout.strip()!=TREE: raise RuntimeError('BASE_TREE_DRIFT')
if run('git','status','--porcelain').stdout.strip(): raise RuntimeError('WORKTREE_DIRTY')

# Freeze hashes are derived from the explicitly AUD-approved pre-R8 decision bridge,
# before any candidate mutation. The files themselves are not modified by R8.
bridge_paths=[
    Path('senecio_polymarket/oracle_runtime/predict_only.py'),
    Path('senecio_polymarket/oracle_runtime/institutional_core_real.py'),
]
bridge_hash={str(p):hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in bridge_paths}

# F1 memory: fetch only fields required by authoritative proof/score across complete history.
p=Path('senecio_polymarket/backend/supabase_client.py'); s=text(p)
s=replace_once(s,
'''AUTHORITY_HISTORY_PAGE_SIZE_MAX = 500\nAUTHORITY_HISTORY_MAX_PAGES = 10_000\n''',
'''AUTHORITY_HISTORY_PAGE_SIZE_MAX = 500\nAUTHORITY_HISTORY_MAX_PAGES = 10_000\n# Authority history intentionally projects only fields consumed by settlement proof\n# and authoritative scoring. Large diagnostic audit payloads are fetched only for\n# the bounded recent-dashboard cache, never for the complete authority cohort.\nAUTHORITY_HISTORY_SELECT = (\n    "id,ts,symbol,prediction,confidence,price_now,outcome,exchange_used,"\n    "origin_price_v1:audit->origin_price_v1,"\n    "outcomes_dual:audit->outcomes_dual"\n)\n\n\ndef _authority_row_from_projection(row: dict[str, Any]) -> dict[str, Any]:\n    projected = dict(row)\n    origin = projected.pop("origin_price_v1", None)\n    dual = projected.pop("outcomes_dual", None)\n    # Unit/compatibility callers may already provide the historical full shape.\n    if origin is None and dual is None and isinstance(projected.get("audit"), dict):\n        audit = projected["audit"]\n        projected["audit"] = {\n            key: audit[key] for key in ("origin_price_v1", "outcomes_dual") if key in audit\n        }\n        return projected\n    audit: dict[str, Any] = {}\n    if isinstance(origin, dict):\n        audit["origin_price_v1"] = origin\n    if isinstance(dual, dict):\n        audit["outcomes_dual"] = dual\n    projected["audit"] = audit\n    return projected\n''','authority constants')
s=replace_once(s,
'''        params = {\n            "limit": str(bounded_page_size),\n            "order": "ts.asc,id.asc",\n        }\n''',
'''        params = {\n            "select": AUTHORITY_HISTORY_SELECT,\n            "limit": str(bounded_page_size),\n            "order": "ts.asc,id.asc",\n        }\n''','authority params')
s=replace_once(s,
'''            seen.add(key)\n            collected.append(row)\n''',
'''            seen.add(key)\n            collected.append(_authority_row_from_projection(row))\n''','authority collection')
write(p,s)

# F1 memory: avoid JSON encode/decode cloning of the whole authority cohort; final
# canonical hashing still sorts keys and is byte deterministic.
p=Path('senecio_polymarket/backend/authority_snapshot.py'); s=text(p)
s=replace_once(s,
'''def _canonical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:\n    normalized = [json.loads(_canonical_json(row).decode()) for row in rows]\n    return sorted(\n        normalized,\n        key=lambda row: (\n            str(row.get("ts") or ""),\n            str(row.get("id") or ""),\n            _canonical_json(row),\n        ),\n    )\n''',
'''def _canonical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:\n    # fetch_authority_history enforces a unique (ts,id) cursor. Deep-copy once;\n    # canonical JSON sorting is deferred to the single content-hash pass.\n    normalized = [copy.deepcopy(row) for row in rows]\n    return sorted(normalized, key=lambda row: (str(row.get("ts") or ""), str(row.get("id") or "")))\n''','canonical rows')
s=replace_once(s,
'''        history_result, count_result = await asyncio.gather(\n            supabase_client.fetch_authority_history(symbol=symbol),\n            supabase_client.count_predictions_exact(),\n            return_exceptions=True,\n        )\n''',
'''        history_result, count_result, recent_result = await asyncio.gather(\n            supabase_client.fetch_authority_history(symbol=symbol),\n            supabase_client.count_predictions_exact(),\n            supabase_client.fetch_predictions(limit=50, symbol=symbol),\n            return_exceptions=True,\n        )\n''','capture gather')
s=replace_once(s,
'''        rows = _canonical_rows(history_result)\n        recent_predictions = tuple(copy.deepcopy(list(reversed(rows[-50:]))))\n        exact_total = int(count_result)\n''',
'''        rows = _canonical_rows(history_result)\n        # Preserve the rich dashboard decision context with one bounded query; it\n        # is explicitly excluded from authority identity and never expands with DB size.\n        if isinstance(recent_result, list):\n            recent_predictions = tuple(copy.deepcopy(recent_result[:50]))\n        else:\n            recent_predictions = tuple(copy.deepcopy(list(reversed(rows[-50:]))))\n        exact_total = int(count_result)\n''','recent cache')
write(p,s)

# F2 readiness truth: one canonical observational helper shared by readyz + market-context.
p=Path('senecio_polymarket/backend/main_real.py'); s=text(p)
old='''@app.get("/readyz")\nasync def readyz(symbol: str = Query(default="BTCUSDT")):\n    """Observational fail-closed readiness over the current shared generation."""\n    normalized = _validate_public_symbol(symbol)\n    try:\n        snap, refresh = authority_store.observe(normalized)\n    except Exception as exc:\n        return JSONResponse(\n            {"status": "not_ready", "probe": "readiness", "reason": type(exc).__name__},\n            status_code=503,\n        )\n    if snap is None:\n        return JSONResponse(\n            {\n                "status": "not_ready",\n                "probe": "readiness",\n                "reason": "NO_VALID_AUTHORITY_GENERATION",\n                "authority_snapshot_id": None,\n                "generation": None,\n                "canonical_sha256": None,\n                **refresh,\n            },\n            status_code=503,\n        )\n    runner = oracle_runner.get_state()\n    checks = {\n        "authority_history_complete": snap.authority_history_complete,\n        "exact_count_complete": snap.exact_count_complete,\n        "provenance_exact": bool(snap.provenance.get("exact")),\n        "oracle_started": bool(runner.get("started_at")),\n        "paper_lock": snap.live_gate.get("trade_mode") == "PAPER" and bool(snap.live_gate.get("live_capital_locked")),\n        "orders_disabled": snap.live_gate.get("orders_enabled", False) is False,\n        "snapshot_fresh": refresh.get("snapshot_stale") is False,\n        "last_refresh_ok": refresh.get("last_refresh_error") is None,\n    }\n    ready = all(checks.values())\n    payload = {\n        "status": "ready" if ready else "not_ready",\n        "probe": "readiness",\n        "checks": checks,\n        "authority_snapshot_id": snap.snapshot_id,\n        "generation": snap.generation,\n        "canonical_sha256": snap.canonical_sha256,\n        "provenance": snap.provenance,\n        **refresh,\n    }\n    return payload if ready else JSONResponse(payload, status_code=503)\n'''
new='''def _readiness_payload(snap, refresh: dict[str, Any]) -> dict[str, Any]:\n    """Canonical observational readiness; performs no network/database I/O."""\n    runner = oracle_runner.get_state()\n    checks = {\n        "authority_history_complete": snap.authority_history_complete,\n        "exact_count_complete": snap.exact_count_complete,\n        "provenance_exact": bool(snap.provenance.get("exact")),\n        "oracle_started": bool(runner.get("started_at")),\n        "paper_lock": snap.live_gate.get("trade_mode") == "PAPER" and bool(snap.live_gate.get("live_capital_locked")),\n        "orders_disabled": snap.live_gate.get("orders_enabled", False) is False,\n        "snapshot_fresh": refresh.get("snapshot_stale") is False,\n        "last_refresh_ok": refresh.get("last_refresh_error") is None,\n    }\n    ready = all(checks.values())\n    return {\n        "status": "ready" if ready else "not_ready",\n        "probe": "readiness",\n        "checks": checks,\n        "authority_snapshot_id": snap.snapshot_id,\n        "generation": snap.generation,\n        "canonical_sha256": snap.canonical_sha256,\n        "provenance": snap.provenance,\n        **refresh,\n    }\n\n\n@app.get("/readyz")\nasync def readyz(symbol: str = Query(default="BTCUSDT")):\n    """Observational fail-closed readiness over the current shared generation."""\n    normalized = _validate_public_symbol(symbol)\n    try:\n        snap, refresh = authority_store.observe(normalized)\n    except Exception as exc:\n        return JSONResponse(\n            {"status": "not_ready", "probe": "readiness", "reason": type(exc).__name__},\n            status_code=503,\n        )\n    if snap is None:\n        return JSONResponse(\n            {\n                "status": "not_ready", "probe": "readiness",\n                "reason": "NO_VALID_AUTHORITY_GENERATION",\n                "authority_snapshot_id": None, "generation": None, "canonical_sha256": None,\n                **refresh,\n            },\n            status_code=503,\n        )\n    payload = _readiness_payload(snap, refresh)\n    return payload if payload["status"] == "ready" else JSONResponse(payload, status_code=503)\n'''
s=replace_once(s,old,new,'readyz helper')
s=replace_once(s,
'''async def market_context(symbol: str = Query(default="BTCUSDT")):\n    snap = await _snapshot(symbol)\n    return {\n''',
'''async def market_context(symbol: str = Query(default="BTCUSDT")):\n    snap = await _snapshot(symbol)\n    refresh = authority_store.refresh_status(snap.symbol)\n    readiness = _readiness_payload(snap, refresh)\n    return {\n        "readiness": readiness,\n''','market readiness')
write(p,s)

# F2 UI: consume readiness from existing market-context poll, no new timer/request.
p=Path('senecio_polymarket/frontend/app.js'); s=text(p)
s=replace_once(s,
'''      const payload = await getJSON('/api/market-context');\n      renderContext(payload);\n      domainSuccess('context');\n''',
'''      const payload = await getJSON('/api/market-context');\n      renderContext(payload);\n      const readiness = payload.readiness && typeof payload.readiness === 'object' ? payload.readiness : {};\n      if (readiness.status === 'ready') domainSuccess('context');\n      else domainFailure('context', new Error(`READINESS_${readiness.status || 'UNKNOWN'}`));\n''','frontend readiness')
write(p,s)

# R8 focused acceptance with fixed hashes from the approved 4b107 bridge.
test=ROOT/'senecio_polymarket/tests/test_order_070_r8.py'
test.write_text(f'''from __future__ import annotations\nimport asyncio, hashlib, inspect, unittest\nfrom pathlib import Path\nfrom unittest import mock\nfrom backend import supabase_client\nfrom backend.authority_snapshot import _canonical_rows\n\nROOT=Path(__file__).resolve().parents[2]\nAPPROVED_BRIDGE_SHA256={{\n    "senecio_polymarket/oracle_runtime/predict_only.py": "{bridge_hash['senecio_polymarket/oracle_runtime/predict_only.py']}",\n    "senecio_polymarket/oracle_runtime/institutional_core_real.py": "{bridge_hash['senecio_polymarket/oracle_runtime/institutional_core_real.py']}",\n}}\n\nclass R8Acceptance(unittest.TestCase):\n    def test_decision_bridge_is_exact_approved_4b107_surface(self):\n        for rel, expected in APPROVED_BRIDGE_SHA256.items():\n            self.assertEqual(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest(), expected)\n\n    def test_authority_query_projects_only_proof_score_fields(self):\n        select=supabase_client.AUTHORITY_HISTORY_SELECT\n        self.assertIn("origin_price_v1:audit->origin_price_v1", select)\n        self.assertIn("outcomes_dual:audit->outcomes_dual", select)\n        self.assertNotIn("pipeline", select)\n        self.assertNotIn("external_markets", select)\n        raw={{"id":1,"ts":"2026-01-01T00:00:00Z","symbol":"BTCUSDT","prediction":"LONG","confidence":0.6,"price_now":1,"outcome":"WIN","exchange_used":"okx","origin_price_v1":{{"version":"origin-price-v1"}},"outcomes_dual":{{"outcome_1h":"WIN"}},"irrelevant":"x"}}\n        out=supabase_client._authority_row_from_projection(raw)\n        self.assertNotIn("origin_price_v1",out); self.assertNotIn("outcomes_dual",out)\n        self.assertEqual(set(out["audit"]),{{"origin_price_v1","outcomes_dual"}})\n\n    def test_canonical_rows_do_not_json_roundtrip_whole_cohort(self):\n        source=inspect.getsource(_canonical_rows)\n        self.assertNotIn("json.loads", source)\n        self.assertNotIn("_canonical_json(row)", source)\n        rows=[{{"ts":"2","id":"2","audit":{{"x":1}}}},{{"ts":"1","id":"1","audit":{{"x":2}}}}]\n        out=_canonical_rows(rows)\n        self.assertEqual([x["id"] for x in out],["1","2"]); self.assertIsNot(out[0],rows[1])\n\n    def test_dashboard_readiness_uses_existing_context_poll_without_new_timer(self):\n        js=(ROOT/'senecio_polymarket/frontend/app.js').read_text()\n        self.assertIn("payload.readiness",js)\n        self.assertIn("READINESS_",js)\n        self.assertNotIn("getJSON('/readyz",js)\n        self.assertEqual(js.count("setInterval(refreshContext, 2000)"),1)\n\n    def test_market_context_exposes_same_readiness_helper(self):\n        src=(ROOT/'senecio_polymarket/backend/main_real.py').read_text()\n        self.assertIn('"readiness": readiness',src)\n        self.assertIn('readiness = _readiness_payload(snap, refresh)',src)\n        self.assertIn('payload = _readiness_payload(snap, refresh)',src)\n\nif __name__=='__main__': unittest.main()\n''')

# Syntax and immutable decision bridge guards before expensive tests.
for rel in ['senecio_polymarket/backend/supabase_client.py','senecio_polymarket/backend/authority_snapshot.py','senecio_polymarket/backend/main_real.py','senecio_polymarket/tests/test_order_070_r8.py']:
    run('python','-m','py_compile',rel)
for rel,h in bridge_hash.items():
    if hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=h: raise RuntimeError('DECISION_BRIDGE_CHANGED')

allowed={
 'senecio_polymarket/backend/supabase_client.py',
 'senecio_polymarket/backend/authority_snapshot.py',
 'senecio_polymarket/backend/main_real.py',
 'senecio_polymarket/frontend/app.js',
 'senecio_polymarket/tests/test_order_070_r8.py',
}
changed=set(run('git','status','--porcelain').stdout.splitlines())
paths={line[3:] for line in changed}
if paths!=allowed: raise RuntimeError(f'R8_PATH_DRIFT:{sorted(paths)}')
print('R8_MATERIALIZED_PATHS='+','.join(sorted(paths)))
print('APPROVED_BRIDGE_HASHES='+str(bridge_hash))
