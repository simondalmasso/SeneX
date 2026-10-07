"""ORDER098 read-only export of decision-time audit evidence for ORDER097.

This module performs GET-only keyset pagination over the persisted prediction
table and emits only the decision-time fields required by ORDER097. It never
exports SENEX settlement outcomes and never reconstructs historical market
context from current/live observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx


DEFAULT_TABLE = "oracle_predictions"
DEFAULT_SYMBOL = "BTCUSDT"
SELECT = "id,ts,symbol,audit"
POLYMARKET_SOURCE = "POLYMARKET_PUBLIC"
POLYMARKET_VERSION = "polymarket-btc-5m-v1"
EXPORT_CONTRACT_V1 = "senex-order098-t0-audit-export-v1"
EXPORT_CONTRACT_V2 = "senex-order098-t0-audit-export-v2"
CAUSAL_HASH_CONTRACT_V2 = "CAUSAL_T0_ALLOWLIST_V2"


class ExportContractError(RuntimeError):
    """Persisted audit export violates the ORDER098 fail-closed contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "").strip()


def _canonical_utc_ts(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ExportContractError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportContractError(f"invalid timestamp: {raw}") from exc
    if parsed.tzinfo is None:
        raise ExportContractError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_epoch(value: object) -> float:
    canonical = _canonical_utc_ts(value)
    return datetime.fromisoformat(canonical.replace("Z", "+00:00")).timestamp()


def _audit_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _finite_probability(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        return None
    return result


def _exact_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _row_id(value: object) -> int:
    result = _exact_int(value)
    if result is None or result < 0:
        raise ExportContractError("prediction id must be a non-negative integer")
    return result


def _source_evidence(row: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "ts": row.get("ts"),
        "symbol": _normalize_symbol(row.get("symbol")),
        "audit": audit,
    }


def project_t0_row_result(row: dict[str, Any]) -> dict[str, Any]:
    """Return an explicit causal projection outcome for one persisted row."""
    if not isinstance(row, dict):
        return {"status": "ERROR", "reason": "ROW_NOT_OBJECT", "row": None}
    if _normalize_symbol(row.get("symbol")) != DEFAULT_SYMBOL:
        return {"status": "EXCLUDED", "reason": "SYMBOL_NOT_TARGET", "row": None}
    try:
        row_id = _row_id(row.get("id"))
    except ExportContractError:
        return {"status": "ERROR", "reason": "PREDICTION_ID_INVALID", "row": None}

    ts = str(row.get("ts") or "").strip()
    if not ts:
        return {"status": "ERROR", "reason": "TIMESTAMP_MISSING", "row": None}
    audit = _audit_object(row.get("audit"))
    if audit is None:
        return {"status": "ERROR", "reason": "AUDIT_MISSING_OR_INVALID", "row": None}

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        return {"status": "ERROR", "reason": "AUDIT_STEP2_MISSING", "row": None}
    raw_up = _finite_probability(step2.get("up_prob"))
    poly_context = step2.get("polymarket_context_v1")
    if raw_up is None:
        return {"status": "ERROR", "reason": "SENEX_UP_PROB_INVALID", "row": None}
    if not isinstance(poly_context, dict):
        return {"status": "ERROR", "reason": "POLYMARKET_CONTEXT_MISSING", "row": None}

    external = audit.get("external_markets_v1")
    poly = external.get("polymarket") if isinstance(external, dict) else None
    if not isinstance(poly, dict):
        return {"status": "EXCLUDED", "reason": "POLYMARKET_MARKET_MISSING", "row": None}
    if poly.get("source") != POLYMARKET_SOURCE:
        return {"status": "EXCLUDED", "reason": "POLYMARKET_SOURCE_MISMATCH", "row": None}
    if poly.get("version") != POLYMARKET_VERSION:
        return {"status": "EXCLUDED", "reason": "POLYMARKET_VERSION_MISMATCH", "row": None}
    if poly.get("eligible_for_prediction") is not True:
        return {"status": "EXCLUDED", "reason": "POLYMARKET_NOT_ELIGIBLE", "row": None}

    slug = str(poly.get("slug") or "").strip()
    condition_id = str(poly.get("condition_id") or "").strip()
    start_ts = _exact_int(poly.get("start_ts"))
    end_ts = _exact_int(poly.get("end_ts"))
    p_market = _finite_probability(poly.get("up_probability"))
    if (
        not slug
        or not condition_id
        or start_ts is None
        or end_ts is None
        or p_market is None
    ):
        return {"status": "ERROR", "reason": "POLYMARKET_IDENTITY_INVALID", "row": None}

    expected_slug = f"btc-updown-5m-{start_ts}"
    if (
        start_ts <= 0
        or start_ts % 300 != 0
        or end_ts != start_ts + 300
        or slug != expected_slug
    ):
        return {"status": "ERROR", "reason": "POLYMARKET_GRID_INVALID", "row": None}

    causal = {
        "id": row_id,
        "ts": ts,
        "symbol": DEFAULT_SYMBOL,
        "audit": {
            "pipeline": {
                "step2_features": {
                    "up_prob": raw_up,
                    "polymarket_context_v1": dict(poly_context),
                }
            },
            "external_markets_v1": {
                "polymarket": {
                    "source": poly.get("source"),
                    "version": poly.get("version"),
                    "eligible_for_prediction": True,
                    "slug": slug,
                    "condition_id": condition_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "up_probability": p_market,
                }
            },
        },
    }
    causal_hash = _sha256_text(_canonical_json(causal))
    persisted_source_hash = _sha256_text(
        _canonical_json(_source_evidence(row, audit))
    )
    # Preserve the v1 source-custody hash semantics for historical tooling.
    # Prospective v2 identity is the separate causal_t0_sha256 below.
    causal["source_audit_sha256"] = persisted_source_hash
    causal["causal_t0_sha256"] = causal_hash
    return {"status": "ACCEPTED", "reason": "OK", "row": causal}


def project_t0_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Backward-compatible wrapper returning only accepted causal rows."""
    result = project_t0_row_result(row)
    return result["row"] if result["status"] == "ACCEPTED" else None


def fetch_full_audit_rows(
    client: httpx.Client,
    *,
    table: str,
    page_size: int = 100,
    symbol: str = DEFAULT_SYMBOL,
    max_prediction_id: int | None = None,
    start_ts: str | None = None,
    end_ts: str | None = None,
) -> list[dict[str, Any]]:
    """GET-only full-audit export using integer-id keyset pagination."""
    if not table or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in table):
        raise ExportContractError("table name is invalid")
    if isinstance(page_size, bool) or not 1 <= int(page_size) <= 100:
        raise ExportContractError("page_size must be between 1 and 100")
    if max_prediction_id is not None:
        max_prediction_id = _row_id(max_prediction_id)
    canonical_start_ts = _canonical_utc_ts(start_ts) if start_ts is not None else None
    canonical_end_ts = _canonical_utc_ts(end_ts) if end_ts is not None else None
    if (
        canonical_start_ts is not None
        and canonical_end_ts is not None
        and _timestamp_epoch(canonical_end_ts) < _timestamp_epoch(canonical_start_ts)
    ):
        raise ExportContractError("end_ts must be at or after start_ts")

    normalized_symbol = _normalize_symbol(symbol)
    collected: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        params: list[tuple[str, str]] = [
            ("select", SELECT),
            ("symbol", f"eq.{normalized_symbol}"),
            ("order", "id.asc"),
            ("limit", str(int(page_size))),
        ]
        if cursor is not None:
            params.append(("id", f"gt.{cursor}"))
        if max_prediction_id is not None:
            params.append(("id", f"lte.{max_prediction_id}"))
        if canonical_start_ts is not None:
            params.append(("ts", f"gte.{canonical_start_ts}"))
        if canonical_end_ts is not None:
            params.append(("ts", f"lte.{canonical_end_ts}"))

        response = client.get(f"/{table}", params=params)
        if response.status_code != 200:
            raise ExportContractError(
                f"read-only export HTTP {response.status_code}: {response.text[:200]}"
            )
        data = response.json()
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ExportContractError("read-only export response must be a list of objects")
        if not data:
            break

        previous = cursor
        for row in data:
            row_id = _row_id(row.get("id"))
            if previous is not None and row_id <= previous:
                raise ExportContractError("prediction ids are not strictly monotonic")
            if max_prediction_id is not None and row_id > max_prediction_id:
                raise ExportContractError("gateway crossed frozen max_prediction_id")
            previous = row_id
            collected.append(row)
        cursor = previous

        if len(data) < int(page_size):
            break

    return collected


def validate_persistence_lineage(
    *,
    receipt_path: Path,
    fetched_rows: list[dict[str, Any]],
    symbol: str,
    start_ts: str,
    end_ts: str,
) -> dict[str, Any]:
    """Prove generated-T0 -> D1 persistence completeness without labels."""
    from senecio_polymarket.backend.prediction_persistence import (
        PredictionPersistenceError,
        PredictionPersistenceStore,
    )

    if not receipt_path.exists():
        raise ExportContractError(
            f"source-to-D1 receipt file missing: {receipt_path}"
        )
    if not fetched_rows:
        raise ExportContractError("prospective persistence lineage has no fetched rows")

    canonical_start = _canonical_utc_ts(start_ts)
    canonical_end = _canonical_utc_ts(end_ts)
    start_epoch = _timestamp_epoch(canonical_start)
    end_epoch = _timestamp_epoch(canonical_end)
    if end_epoch < start_epoch:
        raise ExportContractError("prospective lineage end_ts precedes start_ts")
    target_symbol = _normalize_symbol(symbol)

    try:
        states = PredictionPersistenceStore(path=receipt_path).states()
    except PredictionPersistenceError as exc:
        raise ExportContractError(
            f"source-to-D1 receipt ledger invalid: {exc}"
        ) from exc

    scoped: list[dict[str, Any]] = []
    for state in states:
        prediction = state.get("prediction")
        if not isinstance(prediction, dict):
            continue
        if _normalize_symbol(prediction.get("symbol")) != target_symbol:
            continue
        ts_epoch = _timestamp_epoch(prediction.get("timestamp"))
        if start_epoch <= ts_epoch <= end_epoch:
            scoped.append(state)

    if not scoped:
        raise ExportContractError(
            "source-to-D1 lineage not established for prospective window"
        )

    unresolved = [
        state for state in scoped
        if state.get("status") != "PERSISTED"
    ]
    if unresolved:
        raise ExportContractError(
            f"source-to-D1 lineage has {len(unresolved)} unresolved generated T0 rows"
        )

    fetched_id_list = [_row_id(row.get("id")) for row in fetched_rows]
    fetched_ids = set(fetched_id_list)
    if len(fetched_ids) != len(fetched_id_list):
        raise ExportContractError("D1 snapshot contains duplicate prediction ids")

    persisted_id_list = [
        _row_id(state.get("d1_prediction_id"))
        for state in scoped
        if state.get("d1_prediction_id") is not None
    ]
    persisted_ids = set(persisted_id_list)
    if len(persisted_ids) != len(persisted_id_list):
        raise ExportContractError("source-to-D1 receipts are not one-to-one")

    missing_receipts = sorted(fetched_ids - persisted_ids)
    missing_d1_rows = sorted(persisted_ids - fetched_ids)
    if missing_receipts:
        raise ExportContractError(
            f"source-to-D1 receipt coverage missing persisted ids: {missing_receipts[:8]}"
        )
    if missing_d1_rows:
        raise ExportContractError(
            f"D1 snapshot missing receipt-bound ids: {missing_d1_rows[:8]}"
        )

    fetched_by_id = {
        _row_id(row.get("id")): row
        for row in fetched_rows
    }
    for state in scoped:
        pred_id = _row_id(state.get("d1_prediction_id"))
        original = state.get("prediction")
        if not isinstance(original, dict):
            raise ExportContractError("source-to-D1 receipt lacks original T0 payload")
        receipt_row = {
            "id": pred_id,
            "ts": original.get("timestamp"),
            "symbol": original.get("symbol"),
            "audit": original.get("_audit"),
        }
        receipt_projection = project_t0_row_result(receipt_row)
        d1_projection = project_t0_row_result(fetched_by_id[pred_id])

        if receipt_projection["status"] == "ERROR":
            raise ExportContractError(
                f"receipt original T0 is not causally projectable for id {pred_id}"
            )
        if d1_projection["status"] == "ERROR":
            raise ExportContractError(
                f"D1 row is not causally projectable for id {pred_id}"
            )
        if receipt_projection["status"] != d1_projection["status"]:
            raise ExportContractError(
                f"receipt/D1 scientific eligibility mismatch for id {pred_id}"
            )
        if receipt_projection["status"] == "ACCEPTED":
            if (
                receipt_projection["row"]["causal_t0_sha256"]
                != d1_projection["row"]["causal_t0_sha256"]
            ):
                raise ExportContractError(
                    f"receipt/D1 causal T0 mismatch for id {pred_id}"
                )

    return {
        "contract": "senex-source-to-d1-lineage-v1",
        "receipt_file_sha256": _file_sha256(receipt_path),
        "window_start_ts": canonical_start,
        "window_end_ts": canonical_end,
        "expected_generated_t0": len(scoped),
        "persisted_t0": len(persisted_ids),
        "unresolved_t0": 0,
        "fetched_d1_rows": len(fetched_ids),
    }


def ensure_prospective_output_paths_available(
    output_path: Path,
    manifest_path: Path,
) -> None:
    """Prospective artifacts are immutable: never replace existing paths."""
    existing = [path for path in (output_path, manifest_path) if path.exists()]
    if existing:
        raise ExportContractError(
            "prospective snapshot path already exists; choose a new immutable path: "
            + ", ".join(str(path) for path in existing)
        )


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    ordered = sorted(rows, key=lambda row: _row_id(row.get("id")))
    seen: set[int] = set()
    chunks: list[str] = []
    for row in ordered:
        row_id = _row_id(row.get("id"))
        if row_id in seen:
            raise ExportContractError(f"duplicate exported prediction id: {row_id}")
        seen.add(row_id)
        chunks.append(_canonical_json(row) + "\n")
    Path(path).write_text("".join(chunks), encoding="utf-8")


def _public_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExportContractError("SUPABASE_URL must be an absolute HTTP(S) origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_manifest(
    *,
    source_origin: str,
    table: str,
    symbol: str,
    fetched_rows: list[dict[str, Any]],
    projected_rows: list[dict[str, Any]],
    output_path: Path,
    generated_at: str | None = None,
    max_prediction_id: int | None = None,
    snapshot_start_ts: str | None = None,
    snapshot_end_ts: str | None = None,
    rejection_counts: dict[str, int] | None = None,
    source_to_d1_lineage: dict[str, Any] | None = None,
    contract: str = EXPORT_CONTRACT_V1,
) -> dict[str, Any]:
    source_evidence = []
    for row in sorted(fetched_rows, key=lambda item: _row_id(item.get("id"))):
        audit = _audit_object(row.get("audit"))
        source_evidence.append(
            _source_evidence(row, audit if audit is not None else {})
        )

    ids = [_row_id(row.get("id")) for row in fetched_rows]
    if contract not in {EXPORT_CONTRACT_V1, EXPORT_CONTRACT_V2}:
        raise ExportContractError("unsupported export contract")

    row_hash_field = (
        "causal_t0_sha256" if contract == EXPORT_CONTRACT_V2
        else "source_audit_sha256"
    )
    manifest: dict[str, Any] = {
        "contract": contract,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_origin": _public_origin(source_origin),
        "table": table,
        "symbol": _normalize_symbol(symbol),
        "select": SELECT,
        "pagination": "INTEGER_ID_KEYSET_ASC",
        "http_methods": ["GET"],
        "fetched_rows": len(fetched_rows),
        "projected_rows": len(projected_rows),
        "skipped_rows": len(fetched_rows) - len(projected_rows),
        "first_id": min(ids) if ids else None,
        "last_id": max(ids) if ids else None,
        "output_file_sha256": _file_sha256(output_path),
        "output_row_hashes_sha256": _sha256_text(
            _canonical_json([row.get(row_hash_field) for row in projected_rows])
        ),
    }
    persisted_custody_hash = _sha256_text(_canonical_json(source_evidence))
    if contract == EXPORT_CONTRACT_V1:
        manifest["source_rows_sha256"] = persisted_custody_hash
    else:
        if max_prediction_id is None:
            raise ExportContractError("prospective v2 export requires max_prediction_id")
        if snapshot_start_ts is None:
            raise ExportContractError("prospective v2 export requires snapshot_start_ts")
        if snapshot_end_ts is None:
            raise ExportContractError("prospective v2 export requires snapshot_end_ts")
        if _timestamp_epoch(snapshot_end_ts) < _timestamp_epoch(snapshot_start_ts):
            raise ExportContractError("prospective v2 snapshot_end_ts precedes snapshot_start_ts")
        if not isinstance(source_to_d1_lineage, dict):
            raise ExportContractError("prospective v2 export requires source-to-D1 lineage evidence")
        if source_to_d1_lineage.get("unresolved_t0") != 0:
            raise ExportContractError("prospective v2 source-to-D1 lineage is unresolved")
        manifest.update({
            "snapshot_start_ts": _canonical_utc_ts(snapshot_start_ts),
            "snapshot_end_ts": _canonical_utc_ts(snapshot_end_ts),
            "snapshot_max_prediction_id": _row_id(max_prediction_id),
            "causal_hash_contract": CAUSAL_HASH_CONTRACT_V2,
            "rejection_counts": dict(sorted((rejection_counts or {}).items())),
            "source_to_d1_lineage": dict(source_to_d1_lineage),
            "non_causal_source_rows_sha256": persisted_custody_hash,
            "non_causal_source_rows_hash_semantics": "PERSISTED_CUSTODY_POST_T0_SENSITIVE",
        })
    return manifest


def _headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise ExportContractError(
            "SUPABASE_READ_KEY or SUPABASE_KEY is required"
        )
    headers = {"apikey": api_key}
    if api_key.startswith("eyJ") and api_key.count(".") == 2:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "GET-only export of persisted SENEX decision-time audit evidence "
            "for ORDER097/ORDER098."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--table", default=os.environ.get("SUPABASE_TABLE", DEFAULT_TABLE))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--max-prediction-id",
        type=int,
        help="Freeze prospective export at this inclusive prediction id (enables v2 contract).",
    )
    parser.add_argument(
        "--start-ts",
        help="Inclusive UTC start of the prospective lineage window; required for v2.",
    )
    parser.add_argument(
        "--end-ts",
        help="Inclusive UTC end of the frozen prospective lineage window; required for v2.",
    )
    parser.add_argument(
        "--persistence-receipts",
        type=Path,
        help="Durable source-to-D1 receipt ledger; required for v2.",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    prospective_v2 = args.max_prediction_id is not None
    if prospective_v2:
        if not args.start_ts:
            parser.error("--start-ts is required with --max-prediction-id")
        if not args.end_ts:
            parser.error("--end-ts is required with --max-prediction-id")
        if args.persistence_receipts is None:
            parser.error("--persistence-receipts is required with --max-prediction-id")
        if _timestamp_epoch(args.end_ts) < _timestamp_epoch(args.start_ts):
            parser.error("--end-ts must be at or after --start-ts")
        ensure_prospective_output_paths_available(args.output, args.manifest)
    elif args.start_ts or args.end_ts or args.persistence_receipts is not None:
        parser.error("--start-ts/--end-ts/--persistence-receipts require --max-prediction-id")

    source_url = os.environ.get("SUPABASE_URL", "").strip()
    if not source_url:
        parser.error("SUPABASE_URL is required")
    api_key = (
        os.environ.get("SUPABASE_READ_KEY", "").strip()
        or os.environ.get("SUPABASE_KEY", "").strip()
    )
    if not api_key:
        parser.error("SUPABASE_READ_KEY or SUPABASE_KEY is required")

    base_url = source_url.rstrip("/") + "/rest/v1"
    with httpx.Client(
        base_url=base_url,
        headers=_headers(api_key),
        timeout=args.timeout,
        follow_redirects=False,
    ) as client:
        fetched = fetch_full_audit_rows(
            client,
            table=args.table,
            page_size=args.page_size,
            symbol=args.symbol,
            max_prediction_id=args.max_prediction_id,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
        )

    source_to_d1_lineage = None
    if prospective_v2:
        source_to_d1_lineage = validate_persistence_lineage(
            receipt_path=args.persistence_receipts,
            fetched_rows=fetched,
            symbol=args.symbol,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
        )

    projection_results = [project_t0_row_result(row) for row in fetched]
    fatal = [item for item in projection_results if item["status"] == "ERROR"]
    if args.max_prediction_id is not None and fatal:
        reasons: dict[str, int] = {}
        for item in fatal:
            reason = str(item["reason"])
            reasons[reason] = reasons.get(reason, 0) + 1
        raise ExportContractError(
            "prospective export has missing/corrupt required T0 evidence: "
            + _canonical_json(reasons)
        )

    projected = [
        item["row"]
        for item in projection_results
        if item["status"] == "ACCEPTED" and item["row"] is not None
    ]
    rejection_counts: dict[str, int] = {}
    for item in projection_results:
        if item["status"] == "EXCLUDED":
            reason = str(item["reason"])
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, projected)
    contract = (
        EXPORT_CONTRACT_V2
        if args.max_prediction_id is not None
        else EXPORT_CONTRACT_V1
    )
    manifest = build_manifest(
        source_origin=source_url,
        table=args.table,
        symbol=args.symbol,
        fetched_rows=fetched,
        projected_rows=projected,
        output_path=args.output,
        max_prediction_id=args.max_prediction_id,
        snapshot_start_ts=args.start_ts,
        snapshot_end_ts=args.end_ts,
        rejection_counts=rejection_counts,
        source_to_d1_lineage=source_to_d1_lineage,
        contract=contract,
    )
    manifest["exporter_file_sha256"] = _file_sha256(Path(__file__).resolve())
    args.manifest.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    status = "PASS" if projected else "BLOCKED_NO_ORDER097_T0_ROWS"
    print(json.dumps({
        "status": status,
        "fetched_rows": len(fetched),
        "projected_rows": len(projected),
        "skipped_rows": len(fetched) - len(projected),
        "output": str(args.output),
        "manifest": str(args.manifest),
    }, sort_keys=True))
    return 0 if projected else 2


if __name__ == "__main__":
    raise SystemExit(main())
