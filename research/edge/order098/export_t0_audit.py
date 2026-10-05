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


def project_t0_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Project one persisted row onto the exact decision-time ORDER097 surface.

    Post-T0 settlement/outcome material is deliberately omitted even when it is
    present in the persisted audit JSON.
    """
    if not isinstance(row, dict):
        return None
    if _normalize_symbol(row.get("symbol")) != DEFAULT_SYMBOL:
        return None
    try:
        row_id = _row_id(row.get("id"))
    except ExportContractError:
        return None

    ts = str(row.get("ts") or "").strip()
    if not ts:
        return None
    audit = _audit_object(row.get("audit"))
    if audit is None:
        return None

    pipeline = audit.get("pipeline")
    step2 = pipeline.get("step2_features") if isinstance(pipeline, dict) else None
    if not isinstance(step2, dict):
        return None
    raw_up = _finite_probability(step2.get("up_prob"))
    poly_context = step2.get("polymarket_context_v1")
    if raw_up is None or not isinstance(poly_context, dict):
        return None

    external = audit.get("external_markets_v1")
    poly = external.get("polymarket") if isinstance(external, dict) else None
    if not isinstance(poly, dict):
        return None
    if poly.get("source") != POLYMARKET_SOURCE:
        return None
    if poly.get("version") != POLYMARKET_VERSION:
        return None
    if poly.get("eligible_for_prediction") is not True:
        return None
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
        return None

    evidence_hash = _sha256_text(_canonical_json(_source_evidence(row, audit)))
    return {
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
        "source_audit_sha256": evidence_hash,
    }


def fetch_full_audit_rows(
    client: httpx.Client,
    *,
    table: str,
    page_size: int = 500,
    symbol: str = DEFAULT_SYMBOL,
) -> list[dict[str, Any]]:
    """GET-only full-audit export using the integer primary key as cursor."""
    if not table or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in table):
        raise ExportContractError("table name is invalid")
    if isinstance(page_size, bool) or not 1 <= int(page_size) <= 1000:
        raise ExportContractError("page_size must be between 1 and 1000")

    normalized_symbol = _normalize_symbol(symbol)
    collected: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        params = {
            "select": SELECT,
            "symbol": f"eq.{normalized_symbol}",
            "order": "id.asc",
            "limit": str(int(page_size)),
        }
        if cursor is not None:
            params["id"] = f"gt.{cursor}"

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
            previous = row_id
            collected.append(row)
        cursor = previous

        if len(data) < int(page_size):
            break

    return collected


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


def _env_value(primary: str, *legacy: str) -> str:
    """Resolve a neutral SENEX data variable before legacy compatibility names."""
    for name in (primary, *legacy):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _public_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExportContractError("SENEX_DATA_URL must be an absolute HTTP(S) origin")
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
) -> dict[str, Any]:
    source_evidence = []
    for row in sorted(fetched_rows, key=lambda item: _row_id(item.get("id"))):
        audit = _audit_object(row.get("audit"))
        source_evidence.append(
            _source_evidence(row, audit if audit is not None else {})
        )

    ids = [_row_id(row.get("id")) for row in fetched_rows]
    return {
        "contract": "senex-order098-t0-audit-export-v1",
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
        "source_rows_sha256": _sha256_text(_canonical_json(source_evidence)),
        "output_file_sha256": _file_sha256(output_path),
        "output_row_hashes_sha256": _sha256_text(
            _canonical_json(
                [row.get("source_audit_sha256") for row in projected_rows]
            )
        ),
    }


def _headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise ExportContractError("read-only SENEX data credential is required")
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
    parser.add_argument(
        "--table",
        default=_env_value("SENEX_DATA_TABLE", "SUPABASE_TABLE") or DEFAULT_TABLE,
    )
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    source_url = _env_value("SENEX_DATA_URL", "SUPABASE_URL")
    if not source_url:
        parser.error(
            "SENEX_DATA_URL is required "
            "(legacy SUPABASE_URL is accepted for compatibility)"
        )
    api_key = _env_value(
        "SENEX_DATA_READ_KEY",
        "SENEX_DATA_KEY",
        "SUPABASE_READ_KEY",
        "SUPABASE_KEY",
    )
    if not api_key:
        parser.error(
            "SENEX_DATA_READ_KEY is required "
            "(legacy SUPABASE_READ_KEY/SUPABASE_KEY are accepted for compatibility)"
        )

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
        )

    projected = [
        value for value in (project_t0_row(row) for row in fetched)
        if value is not None
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, projected)
    manifest = build_manifest(
        source_origin=source_url,
        table=args.table,
        symbol=args.symbol,
        fetched_rows=fetched,
        projected_rows=projected,
        output_path=args.output,
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
