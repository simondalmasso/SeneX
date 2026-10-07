from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from senecio_polymarket.backend.prediction_persistence import PredictionPersistenceStore


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


def test_causal_hash_is_invariant_to_post_t0_audit_mutation():
    m = _load()
    a = _row()
    b = _row()
    b["audit"]["outcomes_dual"] = {"outcome_1h": "LOSS", "price_1h_later": 1.0}
    b["audit"]["settlement_proof_v1"] = {"different": True}

    pa = m.project_t0_row(a)
    pb = m.project_t0_row(b)

    assert pa is not None and pb is not None
    assert pa["audit"] == pb["audit"]
    assert pa["causal_t0_sha256"] == pb["causal_t0_sha256"]
    assert pa["source_audit_sha256"] != pb["source_audit_sha256"]


def test_projection_result_distinguishes_missing_audit_from_scientific_exclusion():
    m = _load()
    missing = _row()
    missing["audit"] = None
    excluded = _row()
    excluded["audit"]["external_markets_v1"]["polymarket"]["eligible_for_prediction"] = False

    fatal = m.project_t0_row_result(missing)
    skip = m.project_t0_row_result(excluded)

    assert fatal["status"] == "ERROR"
    assert fatal["reason"] == "AUDIT_MISSING_OR_INVALID"
    assert skip["status"] == "EXCLUDED"
    assert skip["reason"] == "POLYMARKET_NOT_ELIGIBLE"


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


def test_fetch_full_audit_respects_frozen_max_prediction_id():
    m = _load()
    seen_id_filters = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_id_filters.append(request.url.params.get_list("id"))
        if len(seen_id_filters) == 1:
            return httpx.Response(200, json=[_row(1), _row(2)])
        return httpx.Response(200, json=[_row(3)])

    with httpx.Client(
        base_url="https://example.test/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        rows = m.fetch_full_audit_rows(
            client,
            table="oracle_predictions",
            page_size=2,
            symbol="BTCUSDT",
            max_prediction_id=3,
        )

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert seen_id_filters[0] == ["lte.3"]
    assert seen_id_filters[1] == ["gt.2", "lte.3"]


def test_fetch_full_audit_respects_explicit_snapshot_time_window():
    m = _load()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(list(request.url.params.multi_items()))
        return httpx.Response(200, json=[])

    with httpx.Client(
        base_url="https://example.test/rest/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        rows = m.fetch_full_audit_rows(
            client,
            table="oracle_predictions",
            page_size=100,
            symbol="BTCUSDT",
            max_prediction_id=9,
            start_ts="2026-10-06T17:00:00Z",
            end_ts="2026-10-06T18:15:00Z",
        )

    assert rows == []
    # Both lower and upper timestamp filters must be present, not overwritten.
    ts_filters = [value for key, value in seen[0] if key == "ts"]
    assert "gte.2026-10-06T17:00:00Z" in ts_filters
    assert "lte.2026-10-06T18:15:00Z" in ts_filters


def test_source_to_d1_lineage_counts_failed_tail_to_explicit_snapshot_end(tmp_path):
    m = _load()
    receipts = tmp_path / "receipts.jsonl"
    store = PredictionPersistenceStore(path=receipts)

    persisted_prediction = {
        "timestamp": "2026-10-06T18:00:00Z",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
    }
    failed_tail_prediction = {
        "timestamp": "2026-10-06T18:10:00Z",
        "symbol": "BTCUSDT",
        "prediction": "SHORT",
    }
    store.enqueue(
        {"packet_id": "p1", "packet_hash": "1" * 64},
        persisted_prediction,
    )
    store.mark_persisted("1" * 64, 101)
    store.enqueue(
        {"packet_id": "p2", "packet_hash": "2" * 64},
        failed_tail_prediction,
    )
    store.mark_failed("2" * 64, "NETWORK")

    with pytest.raises(m.ExportContractError, match="unresolved generated T0"):
        m.validate_persistence_lineage(
            receipt_path=receipts,
            fetched_rows=[
                {
                    "id": 101,
                    "ts": "2026-10-06T18:00:00Z",
                    "symbol": "BTCUSDT",
                }
            ],
            symbol="BTCUSDT",
            start_ts="2026-10-06T17:55:00Z",
            end_ts="2026-10-06T18:15:00Z",
        )


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


def test_prospective_manifest_records_boundary_and_rejection_reasons(tmp_path):
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
        generated_at="2026-10-06T18:00:00Z",
        max_prediction_id=1234,
        snapshot_start_ts="2026-10-04T05:00:00Z",
        snapshot_end_ts="2026-10-06T18:00:00Z",
        rejection_counts={"POLYMARKET_NOT_ELIGIBLE": 2},
        source_to_d1_lineage={
            "contract": "senex-source-to-d1-lineage-v1",
            "unresolved_t0": 0,
        },
        contract="senex-order098-t0-audit-export-v2",
    )

    assert manifest["contract"] == "senex-order098-t0-audit-export-v2"
    assert manifest["snapshot_max_prediction_id"] == 1234
    assert manifest["snapshot_end_ts"] == "2026-10-06T18:00:00Z"
    assert manifest["rejection_counts"] == {"POLYMARKET_NOT_ELIGIBLE": 2}
    assert manifest["causal_hash_contract"] == "CAUSAL_T0_ALLOWLIST_V2"


def test_full_audit_page_size_is_bounded_to_gateway_contract():
    m = _load()
    with pytest.raises(m.ExportContractError, match="between 1 and 100"):
        m.fetch_full_audit_rows(
            httpx.Client(base_url="https://example.test/rest/v1"),
            table="oracle_predictions",
            page_size=101,
            symbol="BTCUSDT",
        )
