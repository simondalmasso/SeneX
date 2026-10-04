from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order098" / "export_t0_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("order098_export_t0", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(row_id=1, *, outcome="WIN", audit_as_text=False):
    audit = {
        "pipeline": {
            "step2_features": {
                "up_prob": 0.61,
                "polymarket_context_v1": {
                    "directional_use": False,
                    "experiment_enabled": False,
                    "effective_weight": 0.0,
                },
                "future_noise": {"must_not_export": True},
            },
            "step4_ev": {"adjusted_ev": 0.01},
        },
        "external_markets_v1": {
            "polymarket": {
                "source": "POLYMARKET_PUBLIC",
                "version": "polymarket-btc-5m-v1",
                "eligible_for_prediction": True,
                "slug": "btc-updown-5m-1791069600",
                "condition_id": "0xabc",
                "start_ts": 1791069600,
                "end_ts": 1791069900,
                "up_probability": 0.57,
            },
            "other_market": {"irrelevant": True},
        },
        "outcomes_dual": {"future": "must_not_export"},
        "settlement_proof_v1": {"future": "must_not_export"},
    }
    return {
        "id": row_id,
        "ts": "2026-10-03T23:20:30Z",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "outcome": outcome,
        "audit": json.dumps(audit) if audit_as_text else audit,
    }


def test_projection_exports_only_order097_t0_fields_and_ignores_outcome():
    m = _load()
    a = m.project_t0_row(_row(outcome="WIN"))
    b = m.project_t0_row(_row(outcome="LOSS"))

    assert a is not None and b is not None
    assert a["id"] == 1
    assert a["ts"] == "2026-10-03T23:20:30Z"
    assert a["symbol"] == "BTCUSDT"
    assert a["audit"] == b["audit"]
    assert a["source_audit_sha256"] == b["source_audit_sha256"]
    assert set(a["audit"]) == {"pipeline", "external_markets_v1"}
    assert set(a["audit"]["pipeline"]["step2_features"]) == {
        "up_prob",
        "polymarket_context_v1",
    }
    assert set(a["audit"]["external_markets_v1"]) == {"polymarket"}
    encoded = json.dumps(a, sort_keys=True)
    assert "outcome" not in encoded
    assert "settlement_proof" not in encoded
    assert "future_noise" not in encoded
    assert "other_market" not in encoded


def test_projection_accepts_json_text_audit():
    m = _load()
    result = m.project_t0_row(_row(audit_as_text=True))
    assert result is not None
    assert result["audit"]["pipeline"]["step2_features"]["up_prob"] == 0.61


@pytest.mark.parametrize(
    "mutator",
    [
        lambda row: row.update({"symbol": "ETHUSDT"}),
        lambda row: row.update({"audit": None}),
        lambda row: row["audit"].pop("pipeline"),
        lambda row: row["audit"].pop("external_markets_v1"),
        lambda row: row["audit"]["pipeline"].pop("step2_features"),
        lambda row: row["audit"]["external_markets_v1"].pop("polymarket"),
    ],
)
def test_projection_skips_rows_without_required_t0_structure(mutator):
    m = _load()
    row = _row()
    mutator(row)
    assert m.project_t0_row(row) is None


def test_fetch_full_audit_uses_get_only_and_keyset_pagination():
    m = _load()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.method == "GET"
        params = request.url.params
        assert params["select"] == "id,ts,symbol,audit"
        assert params["symbol"] == "eq.BTCUSDT"
        assert params["order"] == "id.asc"
        if "id" not in params:
            return httpx.Response(200, json=[_row(1), _row(2)])
        assert params["id"] == "gt.2"
        return httpx.Response(200, json=[_row(3)])

    transport = httpx.MockTransport(handler)
    with httpx.Client(
        base_url="https://example.test/rest/v1",
        transport=transport,
        headers={"apikey": "secret-not-for-output"},
    ) as client:
        rows = m.fetch_full_audit_rows(
            client,
            table="oracle_predictions",
            page_size=2,
            symbol="BTCUSDT",
        )

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert len(calls) == 2


def test_fetch_full_audit_fails_closed_on_non_monotonic_id():
    m = _load()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_row(2), _row(1)])

    with httpx.Client(
        base_url="https://example.test/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(m.ExportContractError, match="monotonic"):
            m.fetch_full_audit_rows(
                client,
                table="oracle_predictions",
                page_size=50,
                symbol="BTCUSDT",
            )


def test_manifest_contains_hashes_and_no_credentials(tmp_path):
    m = _load()
    raw = [_row(1), _row(2)]
    projected = [m.project_t0_row(row) for row in raw]
    projected = [row for row in projected if row is not None]
    output = tmp_path / "t0.jsonl"
    m.write_jsonl(output, projected)

    manifest = m.build_manifest(
        source_origin="https://example.test",
        table="oracle_predictions",
        symbol="BTCUSDT",
        fetched_rows=raw,
        projected_rows=projected,
        output_path=output,
        generated_at="2026-10-04T03:00:00Z",
    )

    assert manifest["fetched_rows"] == 2
    assert manifest["projected_rows"] == 2
    assert manifest["skipped_rows"] == 0
    assert len(manifest["source_rows_sha256"]) == 64
    assert len(manifest["output_file_sha256"]) == 64
    encoded = json.dumps(manifest, sort_keys=True)
    assert "secret" not in encoded.lower()
    assert "apikey" not in encoded.lower()
    assert "authorization" not in encoded.lower()
