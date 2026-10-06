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
