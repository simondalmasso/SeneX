
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "cloudflare" / "senex-order072-d1-gateway" / "src" / "gateway_order074.js"
WRANGLER = ROOT / "cloudflare" / "senex-order072-d1-gateway" / "wrangler.jsonc"


def _node_case(tmp_path: Path, body: str) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is required for gateway behavior tests")
    module_path = tmp_path / "gateway.mjs"
    module_path.write_text(GATEWAY.read_text(encoding="utf-8"), encoding="utf-8")
    script = tmp_path / "case.mjs"
    script.write_text(
        f'import worker from {json.dumps(module_path.as_uri())};\n' + body,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _env_js() -> str:
    return """
const db = {
  prepare(sql) {
    return {
      bind(...args) {
        return {
          async all() { return {results: []}; },
          async first() {
            if (sql.includes("MAX(id)")) return {m: 0};
            return null;
          },
          async run() { return {success: true}; },
        };
      },
      async first() {
        if (sql.includes("MAX(id)")) return {m: 0};
        return null;
      },
    };
  },
};
"""


def test_gateway_has_no_source_embedded_credential_and_writes_default_off() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    wrangler = WRANGLER.read_text(encoding="utf-8")
    assert "643935c88358867802d8d8f5e1599cdda76ec62bc36d352e067806342fe88d07" not in source
    assert "GATEWAY_READ_TOKEN" in source
    assert "GATEWAY_WRITE_TOKEN" in source
    assert 'env.D1_WRITES_ENABLED === "1"' in source
    assert '"D1_WRITES_ENABLED": "0"' in wrangler


def test_read_credential_cannot_write(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        _env_js()
        + """
const env = {HOT: db, COLD: db, GATEWAY_READ_TOKEN: "read", GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"};
const req = new Request("https://unit/rest/v1/oracle_predictions", {
  method: "POST",
  headers: {"apikey": "read", "content-type": "application/json"},
  body: "{}",
});
const res = await worker.fetch(req, env);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 403, "body": {"error": "write_unauthorized"}}


def test_missing_write_enablement_fails_closed(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        _env_js()
        + """
const env = {HOT: db, COLD: db, GATEWAY_WRITE_TOKEN: "write"};
const req = new Request("https://unit/rest/v1/oracle_predictions", {
  method: "POST",
  headers: {"apikey": "write", "content-type": "application/json"},
  body: "{}",
});
const res = await worker.fetch(req, env);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 503, "body": {"error": "writer_disabled"}}


@pytest.mark.parametrize(
    "url,prefer",
    [
        ("https://unit/rest/v1/oracle_predictions?select=id&limit=501", ""),
        ("https://unit/rest/v1/oracle_predictions?select=id&offset=1001", ""),
        ("https://unit/rest/v1/oracle_predictions?select=id", "count=exact"),
        ("https://unit/rest/v1/oracle_predictions?select=*&limit=101", ""),
    ],
)
def test_expensive_reads_fail_before_db(tmp_path: Path, url: str, prefer: str) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_READ_TOKEN: "read"};
const headers = {"apikey": "read"};
"""
        + (f'headers["prefer"] = {json.dumps(prefer)};\n' if prefer else "")
        + f"""
const res = await worker.fetch(new Request({json.dumps(url)}, {{headers}}), env);
const body = await res.json();
console.log(JSON.stringify({{status: res.status, body}}));
""",
    )
    assert case["status"] == 500
    assert case["body"]["error"] == "request_failed"
    assert "DB_TOUCHED" not in case["body"]["message"]


def test_patch_cannot_modify_t0_field(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"};
const req = new Request("https://unit/rest/v1/oracle_predictions?id=eq.7", {
  method: "PATCH",
  headers: {"apikey": "write", "content-type": "application/json"},
  body: JSON.stringify({prediction: "SHORT"}),
});
const res = await worker.fetch(req, env);
const body = await res.json();
console.log(JSON.stringify({status: res.status, body}));
""",
    )
    assert case["status"] == 500
    assert "immutable T0 field" in case["body"]["message"]
    assert "DB_TOUCHED" not in case["body"]["message"]


def test_admin_requires_write_scope(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_READ_TOKEN: "read", GATEWAY_WRITE_TOKEN: "write"};
const res = await worker.fetch(
  new Request("https://unit/admin/digests", {headers: {"apikey": "read"}}),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 403, "body": {"error": "admin_unauthorized"}}


def test_bounded_read_still_works_with_read_scope(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        _env_js()
        + """
const env = {HOT: db, COLD: db, GATEWAY_READ_TOKEN: "read"};
const res = await worker.fetch(
  new Request("https://unit/rest/v1/oracle_predictions?select=id&limit=1", {headers: {"apikey": "read"}}),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 200, "body": []}


def test_explicit_write_scope_and_enablement_reaches_writer(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        _env_js()
        + """
const env = {HOT: db, COLD: db, GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"};
const payload = {
  ts: "2026-10-05T00:00:00Z",
  symbol: "BTCUSDT",
  prediction: "LONG",
  confidence: 0.7,
  ev: 0.01,
  price_now: 100,
  price_15m_later: null,
  outcome: null,
  exchange_used: "okx",
  audit: {
    origin_price_v1: {
      version: "origin-price-v1",
      price: 100,
      timestamp: "2026-10-05T00:00:00Z",
      source: "okx",
    },
  },
};
const res = await worker.fetch(new Request("https://unit/rest/v1/oracle_predictions", {
  method: "POST",
  headers: {"apikey": "write", "content-type": "application/json"},
  body: JSON.stringify(payload),
}), env);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case["status"] == 201
    assert len(case["body"]) == 1
    assert case["body"][0]["symbol"] == "BTCUSDT"


def test_legacy_generic_gateway_token_is_not_an_active_read_scope(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_TOKEN: "legacy"};
const res = await worker.fetch(
  new Request("https://unit/rest/v1/oracle_predictions?select=id&limit=1", {
    headers: {"apikey": "legacy"},
  }),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 401, "body": {"error": "unauthorized"}}


@pytest.mark.parametrize(
    "url",
    [
        "https://unit/rest/v1/oracle_predictions",
        "https://unit/rest/v1/oracle_predictions?outcome=is.null&audit->outcomes_dual=is.null",
        "https://unit/rest/v1/oracle_predictions?id=gt.7&outcome=is.null&audit->outcomes_dual=is.null",
        "https://unit/rest/v1/oracle_predictions?id=eq.7",
        "https://unit/rest/v1/oracle_predictions?id=eq.7&outcome=is.null",
    ],
)
def test_patch_requires_exact_single_id_and_cas_before_db(tmp_path: Path, url: str) -> None:
    case = _node_case(
        tmp_path,
        f"""
const bomb = {{prepare() {{ throw new Error("DB_TOUCHED"); }}}};
const env = {{HOT: bomb, COLD: bomb, GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"}};
const req = new Request({json.dumps(url)}, {{
  method: "PATCH",
  headers: {{"apikey": "write", "content-type": "application/json"}},
  body: JSON.stringify({{outcome: "WIN", audit: {{outcomes_dual: {{v: 1}}}}}}),
}});
const res = await worker.fetch(req, env);
const body = await res.json();
console.log(JSON.stringify({{status: res.status, body}}));
""",
    )
    assert case["status"] == 500
    assert "DB_TOUCHED" not in case["body"]["message"]
    assert "patch target" in case["body"]["message"].lower()


def test_valid_primary_settlement_cas_reaches_db(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"};
const req = new Request(
  "https://unit/rest/v1/oracle_predictions?id=eq.7&outcome=is.null&audit->outcomes_dual=is.null",
  {
    method: "PATCH",
    headers: {"apikey": "write", "content-type": "application/json"},
    body: JSON.stringify({outcome: "WIN", audit: {outcomes_dual: {v: 1}}}),
  },
);
const res = await worker.fetch(req, env);
const body = await res.json();
console.log(JSON.stringify({status: res.status, body}));
""",
    )
    assert case["status"] == 500
    assert case["body"]["message"] == "DB_TOUCHED"


def test_order098_full_audit_query_shape_at_bounded_limit_100(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        _env_js()
        + """
const env = {HOT: db, COLD: db, GATEWAY_READ_TOKEN: "read"};
const res = await worker.fetch(
  new Request(
    "https://unit/rest/v1/oracle_predictions?select=id,ts,symbol,audit&symbol=eq.BTCUSDT&order=id.asc&limit=100",
    {headers: {"apikey": "read"}},
  ),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 200, "body": []}


def test_valid_repair_cas_reaches_db(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
const bomb = {prepare() { throw new Error("DB_TOUCHED"); }};
const env = {HOT: bomb, COLD: bomb, GATEWAY_WRITE_TOKEN: "write", D1_WRITES_ENABLED: "1"};
const req = new Request(
  "https://unit/rest/v1/oracle_predictions?id=eq.7&outcome=eq.WIN&audit->outcomes_dual=is.null",
  {
    method: "PATCH",
    headers: {"apikey": "write", "content-type": "application/json"},
    body: JSON.stringify({price_15m_later: 101, audit: {outcomes_dual: {v: 1}}}),
  },
);
const res = await worker.fetch(req, env);
const body = await res.json();
console.log(JSON.stringify({status: res.status, body}));
""",
    )
    assert case["status"] == 500
    assert case["body"]["message"] == "DB_TOUCHED"


def test_full_audit_hydrates_only_matching_hot_cold_generation(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf), b => b.toString(16).padStart(2, "0")).join("");
}
const audit = {marker: "v1"};
const auditJson = JSON.stringify(audit);
const auditDigest = await sha(auditJson);
const payload = '{"audit":' + auditJson + ',"id":7,"schema":"senex-r2-audit-v1","version":1}';
const payloadSha = await sha(payload);
const coldKey = "senex/order071/oracle_predictions/7/" + auditDigest + ".json";
const hot = {
  id: 7, ts: "2026-10-05T00:00:00.000000Z", symbol: "BTCUSDT",
  prediction: "LONG", confidence: 0.7, ev: 0.01, price_now: 100,
  price_15m_later: null, outcome: null, exchange_used: "okx",
  created_at: "2026-10-05T00:00:00.000000Z",
  origin_price_v1_json: null, outcomes_dual_json: null,
  audit_digest: auditDigest, cold_location: "D1_COLD",
  cold_key: coldKey, cold_payload_sha256: payloadSha, source_row_digest: "src"
};
const cold = {
  prediction_id: 7, cold_key: coldKey, schema_version: "senex-r2-audit-v1",
  audit_digest: auditDigest, payload_sha256: payloadSha, payload
};
const HOT = {prepare() { return {bind() { return {async all() { return {results: [hot]}; }}; }}; }};
const COLD = {prepare() { return {bind() { return {async all() { return {results: [cold]}; }}; }}; }};
const env = {HOT, COLD, GATEWAY_READ_TOKEN: "read"};
const res = await worker.fetch(
  new Request("https://unit/rest/v1/oracle_predictions?select=id,audit&id=eq.7&limit=1", {
    headers: {"apikey": "read"},
  }),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case == {"status": 200, "body": [{"id": 7, "audit": {"marker": "v1"}}]}


def test_full_audit_rejects_cross_generation_hot_cold_reference_mismatch(tmp_path: Path) -> None:
    case = _node_case(
        tmp_path,
        """
async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf), b => b.toString(16).padStart(2, "0")).join("");
}
const audit = {marker: "new-cold"};
const auditJson = JSON.stringify(audit);
const auditDigest = await sha(auditJson);
const payload = '{"audit":' + auditJson + ',"id":7,"schema":"senex-r2-audit-v1","version":1}';
const payloadSha = await sha(payload);
const coldKey = "senex/order071/oracle_predictions/7/" + auditDigest + ".json";
const hot = {
  id: 7, ts: "2026-10-05T00:00:00.000000Z", symbol: "BTCUSDT",
  prediction: "LONG", confidence: 0.7, ev: 0.01, price_now: 100,
  price_15m_later: null, outcome: null, exchange_used: "okx",
  created_at: "2026-10-05T00:00:00.000000Z",
  origin_price_v1_json: null, outcomes_dual_json: null,
  audit_digest: "old-hot-digest", cold_location: "D1_COLD",
  cold_key: "old-hot-key", cold_payload_sha256: "old-hot-payload", source_row_digest: "src"
};
const cold = {
  prediction_id: 7, cold_key: coldKey, schema_version: "senex-r2-audit-v1",
  audit_digest: auditDigest, payload_sha256: payloadSha, payload
};
const HOT = {prepare() { return {bind() { return {async all() { return {results: [hot]}; }}; }}; }};
const COLD = {prepare() { return {bind() { return {async all() { return {results: [cold]}; }}; }}; }};
const env = {HOT, COLD, GATEWAY_READ_TOKEN: "read"};
const res = await worker.fetch(
  new Request("https://unit/rest/v1/oracle_predictions?select=id,audit&id=eq.7&limit=1", {
    headers: {"apikey": "read"},
  }),
  env,
);
console.log(JSON.stringify({status: res.status, body: await res.json()}));
""",
    )
    assert case["status"] == 500
    assert case["body"]["error"] == "request_failed"
    assert "reference mismatch" in case["body"]["message"].lower()
