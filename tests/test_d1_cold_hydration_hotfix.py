from __future__ import annotations

import ast
import asyncio
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from senecio_polymarket.backend import authority_seal as aseal
from senecio_polymarket.backend import authority_snapshot
from senecio_polymarket.backend import supabase_client as sc


IDENTITY = {
    "source_commit": "a" * 40,
    "source_tree": "b" * 40,
    "build_digest": "sha256:" + "c" * 64,
}
ROW = {
    "id": "1",
    "ts": "2026-09-22T09:00:00+00:00",
    "symbol": "BTCUSDT",
    "prediction": "LONG",
}
CURSOR = {"ts": ROW["ts"], "id": ROW["id"]}


def test_shadow_learning_projection_never_requests_full_audit():
    path = Path("senecio_polymarket/oracle_runtime/institutional_core_real.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    projection = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SHADOW_FETCH_PROJECTION"
            for target in node.targets
        ):
            projection = tuple(ast.literal_eval(node.value))
            break
    assert projection is not None
    assert "audit" not in projection
    assert "origin_price_v1:audit->origin_price_v1" in projection
    assert "outcomes_dual:audit->outcomes_dual" in projection
    assert "exchange_used" in projection


def test_list_authority_scopes_round_trip_uses_durable_seal_contract():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
        os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
    ):
        aseal.save_authority_state(
            "BTCUSDT",
            [ROW],
            CURSOR,
            identity=IDENTITY,
            writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
        )
        assert aseal.list_authority_scopes() == ["BTCUSDT"]


def test_mutable_refresh_preserves_sealed_timestamp_representation():
    row = {
        "id": "1",
        "ts": "2026-09-22T09:00:00Z",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.7,
        "price_now": 100.0,
        "outcome": None,
        "exchange_used": "test",
        "audit": {},
    }
    cursor = {"ts": row["ts"], "id": row["id"]}
    refreshed = {**row, "ts": "2026-09-22T09:00:00+00:00"}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
        os.environ, {"SENEX_AUTHORITY_SEAL_DIR": tmp}, clear=False
    ):
        sc.reset_r7b_incremental_state_for_tests()
        aseal.save_authority_state(
            "BTCUSDT",
            [row],
            cursor,
            identity=IDENTITY,
            writer_contract=sc.AUTHORITY_MUTATION_CONTRACT,
        )
        with mock.patch.object(
            sc, "internal_identity_projection", return_value=IDENTITY
        ), mock.patch.object(
            sc, "_fetch_authority_delta_raw", new=mock.AsyncMock(return_value=[])
        ), mock.patch.object(
            sc, "_refresh_mutable_rows", new=mock.AsyncMock(return_value=[refreshed])
        ):
            rows = asyncio.run(sc.fetch_authority_history("BTCUSDT"))
    assert rows[0]["ts"] == "2026-09-22T09:00:00Z"


def test_snapshot_refresh_preserves_exact_authority_history_reason():
    store = authority_snapshot.AuthoritySnapshotStore(ttl_s=60)
    history_error = sc.AuthorityHistoryIncompleteError(
        "AUTHORITY_MUTABLE_ROW_DISAPPEARED"
    )
    with mock.patch.object(
        authority_snapshot.supabase_client,
        "fetch_authority_history",
        new=mock.AsyncMock(side_effect=history_error),
    ), mock.patch.object(
        authority_snapshot.supabase_client,
        "count_predictions_exact",
        new=mock.AsyncMock(return_value=1),
    ):
        with pytest.raises(authority_snapshot.AuthoritySnapshotRefreshError) as exc:
            asyncio.run(
                store._capture_complete(
                    "BTCUSDT",
                    lambda score: {"unlocked": False},
                )
            )
    assert "AUTHORITY_MUTABLE_ROW_DISAPPEARED" in str(exc.value)
