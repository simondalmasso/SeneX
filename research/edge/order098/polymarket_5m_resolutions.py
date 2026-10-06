"""ORDER098 auditable Polymarket BTC Up/Down 5m resolution corpus builder.

Research-only public-data collector. It never uses a SENEX outcome as a label,
never touches runtime/trading surfaces, and fails closed on ambiguous or
non-terminal resolution evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx


GAMMA_BASE = "https://gamma-api.polymarket.com"
SOURCE = "POLYMARKET_GAMMA_RESOLVED_V1"
POLYMARKET_SOURCE = "POLYMARKET_PUBLIC"
POLYMARKET_VERSION = "polymarket-btc-5m-v1"
TARGET_HORIZON_SECONDS = 300


class ResolutionEvidenceError(ValueError):
    """Public resolution evidence violates the ORDER098 contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _exact_epoch_second(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} is not an exact epoch second")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValueError(f"{field} is not an exact epoch second")
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return int(raw)
    raise ValueError(f"{field} is not an exact epoch second")


def _parse_time(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} is invalid")
    if isinstance(value, (int, float)):
        result = float(value)
        if math.isfinite(result):
            return result
        raise ValueError(f"{field} is invalid")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is missing")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} is not ISO8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def _slug_start(slug: str) -> int | None:
    prefix = "btc-updown-5m-"
    if not isinstance(slug, str) or not slug.startswith(prefix):
        return None
    suffix = slug[len(prefix):]
    if not suffix.isdigit():
        return None
    start = int(suffix)
    if start % TARGET_HORIZON_SECONDS != 0:
        return None
    return start


def extract_market_identity(row: dict) -> dict[str, object] | None:
    """Extract only the exact Polymarket target identity from a SENEX T0 row."""
    if not isinstance(row, dict):
        return None
    symbol = str(row.get("symbol") or "").replace("/", "").upper()
    if symbol != "BTCUSDT":
        return None

    audit = row.get("_audit")
    if not isinstance(audit, dict):
        audit = row.get("audit")
    external = audit.get("external_markets_v1") if isinstance(audit, dict) else None
    poly = external.get("polymarket") if isinstance(external, dict) else None
    if not isinstance(poly, dict):
        return None
    if poly.get("source") != POLYMARKET_SOURCE:
        return None
    if poly.get("version") != POLYMARKET_VERSION:
        return None
    if poly.get("eligible_for_prediction") is not True:
        return None

    slug = str(poly.get("slug") or "")
    condition_id = str(poly.get("condition_id") or "")
    if not slug or not condition_id:
        return None
    try:
        start_ts = _exact_epoch_second(poly.get("start_ts"), "start_ts")
        end_ts = _exact_epoch_second(poly.get("end_ts"), "end_ts")
    except ValueError:
        return None
    if _slug_start(slug) != start_ts:
        return None
    if end_ts - start_ts != TARGET_HORIZON_SECONDS:
        return None

    return {
        "slug": slug,
        "condition_id": condition_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


def extract_market_identities(rows: Iterable[dict]) -> list[dict[str, object]]:
    by_slug: dict[str, dict[str, object]] = {}
    for row in rows:
        identity = extract_market_identity(row)
        if identity is None:
            continue
        slug = str(identity["slug"])
        existing = by_slug.get(slug)
        if existing is not None and existing != identity:
            raise ResolutionEvidenceError(
                f"conflicting target identity for slug {slug}"
            )
        by_slug[slug] = identity
    return sorted(
        by_slug.values(),
        key=lambda item: (int(item["start_ts"]), str(item["slug"])),
    )


def _select_market(
    event: dict,
    slug: str,
    expected_condition_id: str | None,
) -> dict:
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise ResolutionEvidenceError("event markets are missing")
    candidates = [
        market for market in markets
        if isinstance(market, dict) and str(market.get("slug") or "") == slug
    ]
    if expected_condition_id is not None:
        candidates = [
            market for market in candidates
            if str(market.get("conditionId") or market.get("condition_id") or "")
            == expected_condition_id
        ]
    if len(candidates) != 1:
        if expected_condition_id is not None:
            raise ResolutionEvidenceError(
                "condition identity is missing, mismatched, or ambiguous"
            )
        raise ResolutionEvidenceError("market identity is missing or ambiguous")
    return candidates[0]


def normalize_gamma_event(
    event: dict,
    *,
    expected_slug: str | None = None,
    expected_condition_id: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, object]:
    """Normalize one closed Gamma event into an ORDER097-compatible label."""
    if not isinstance(event, dict):
        raise ResolutionEvidenceError("Gamma event must be an object")
    event_slug = str(event.get("slug") or "")
    slug = expected_slug or event_slug
    if not slug or event_slug != slug:
        raise ResolutionEvidenceError("event slug identity mismatch")
    start_ts = _slug_start(slug)
    if start_ts is None:
        raise ResolutionEvidenceError("slug is not an integral BTC 5m target")
    end_ts = start_ts + TARGET_HORIZON_SECONDS

    if event.get("closed") is not True:
        raise ResolutionEvidenceError("event is not closed")

    market = _select_market(event, slug, expected_condition_id)
    condition_id = str(
        market.get("conditionId") or market.get("condition_id") or ""
    )
    if not condition_id:
        raise ResolutionEvidenceError("condition identity is missing")
    if expected_condition_id is not None and condition_id != expected_condition_id:
        raise ResolutionEvidenceError("condition identity mismatch")
    if market.get("closed") is not True:
        raise ResolutionEvidenceError("market is not closed")
    if str(market.get("umaResolutionStatus") or "").lower() != "resolved":
        raise ResolutionEvidenceError("market is not resolved")

    outcomes = [str(item).strip().upper() for item in _json_list(market.get("outcomes"))]
    raw_prices = _json_list(market.get("outcomePrices"))
    if len(outcomes) != 2 or set(outcomes) != {"UP", "DOWN"} or len(raw_prices) != 2:
        raise ResolutionEvidenceError("terminal outcome schema is invalid")
    try:
        prices = [float(value) for value in raw_prices]
    except (TypeError, ValueError) as exc:
        raise ResolutionEvidenceError("terminal outcome prices are invalid") from exc
    if any(not math.isfinite(value) for value in prices):
        raise ResolutionEvidenceError("terminal outcome prices are invalid")

    winners = [
        index for index, value in enumerate(prices)
        if abs(value - 1.0) <= 1e-12
    ]
    losers = [
        index for index, value in enumerate(prices)
        if abs(value) <= 1e-12
    ]
    if len(winners) != 1 or len(losers) != 1 or winners[0] == losers[0]:
        raise ResolutionEvidenceError(
            "terminal outcome prices must be exactly one winner and one loser"
        )
    winner = outcomes[winners[0]]
    if winner not in {"UP", "DOWN"}:
        raise ResolutionEvidenceError("terminal winner is invalid")

    metadata = event.get("eventMetadata")
    final_price = None
    price_to_beat = None
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ResolutionEvidenceError("event metadata is invalid")
        if "finalPrice" not in metadata or "priceToBeat" not in metadata:
            raise ResolutionEvidenceError(
                "event metadata finalPrice/priceToBeat is incomplete"
            )
        try:
            final_price = float(metadata["finalPrice"])
            price_to_beat = float(metadata["priceToBeat"])
        except (TypeError, ValueError) as exc:
            raise ResolutionEvidenceError(
                "event metadata finalPrice/priceToBeat is invalid"
            ) from exc
        if not math.isfinite(final_price) or not math.isfinite(price_to_beat):
            raise ResolutionEvidenceError(
                "event metadata finalPrice/priceToBeat is non-finite"
            )
        metadata_winner = "UP" if final_price >= price_to_beat else "DOWN"
        if metadata_winner != winner:
            raise ResolutionEvidenceError(
                "terminal winner conflicts with finalPrice/priceToBeat evidence"
            )

    timestamp_values: list[float] = []
    for field in ("closedTime", "umaEndDate"):
        raw = market.get(field)
        if raw is None:
            continue
        try:
            parsed = _parse_time(raw, field)
        except ValueError as exc:
            raise ResolutionEvidenceError(str(exc)) from exc
        if parsed < end_ts:
            raise ResolutionEvidenceError(
                f"{field} resolution timestamp is before market end"
            )
        timestamp_values.append(parsed)
    if not timestamp_values:
        raise ResolutionEvidenceError("resolution timestamp is missing")
    resolved_at = max(timestamp_values)

    resolution_source = str(
        market.get("resolutionSource")
        or event.get("resolutionSource")
        or ""
    ).strip()
    if not resolution_source:
        raise ResolutionEvidenceError("resolution source is missing")

    evidence_sha = _sha256_text(_canonical_json(event))
    return {
        "slug": slug,
        "condition_id": condition_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "outcome": winner,
        "resolved_at": resolved_at,
        "source": SOURCE,
        "resolution_source_url": resolution_source,
        "gamma_event_id": event.get("id"),
        "gamma_market_id": market.get("id"),
        "terminal_outcome_prices": prices,
        "final_price": final_price,
        "price_to_beat": price_to_beat,
        "evidence_sha256": evidence_sha,
        "fetched_at": fetched_at,
    }


def read_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSONL at {path}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row at {path}:{line_number} is not an object")
        rows.append(row)
    return rows


def write_jsonl(path: str | Path, records: Iterable[dict]) -> None:
    ordered = sorted(
        records,
        key=lambda row: (
            int(row["start_ts"]),
            str(row["slug"]),
            str(row["condition_id"]),
        ),
    )
    text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in ordered
    )
    Path(path).write_text(text, encoding="utf-8")


def _slug_identity(slug: str) -> dict[str, object]:
    start_ts = _slug_start(slug)
    if start_ts is None:
        raise ResolutionEvidenceError(
            f"invalid BTC 5m slug: {slug!r}"
        )
    return {
        "slug": slug,
        "condition_id": None,
        "start_ts": start_ts,
        "end_ts": start_ts + TARGET_HORIZON_SECONDS,
    }


def _merge_identities(
    identities: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    by_slug: dict[str, dict[str, object]] = {}
    for identity in identities:
        slug = str(identity["slug"])
        existing = by_slug.get(slug)
        if existing is None:
            by_slug[slug] = dict(identity)
            continue
        old_condition = existing.get("condition_id")
        new_condition = identity.get("condition_id")
        if old_condition and new_condition and old_condition != new_condition:
            raise ResolutionEvidenceError(
                f"conflicting target identity for slug {slug}"
            )
        if not old_condition and new_condition:
            existing["condition_id"] = new_condition
    return sorted(
        by_slug.values(),
        key=lambda item: (int(item["start_ts"]), str(item["slug"])),
    )


def collect_resolutions(
    identities: Iterable[dict[str, object]],
    *,
    timeout_s: float = 10.0,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Fetch exact public Gamma records sequentially and normalize them."""
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
        for identity in _merge_identities(identities):
            slug = str(identity["slug"])
            url = f"{GAMMA_BASE}/events/slug/{slug}"
            try:
                response = client.get(url)
                response.raise_for_status()
                event = response.json()
                record = normalize_gamma_event(
                    event,
                    expected_slug=slug,
                    expected_condition_id=(
                        str(identity["condition_id"])
                        if identity.get("condition_id")
                        else None
                    ),
                    fetched_at=fetched_at,
                )
            except Exception as exc:
                rejected.append({
                    "slug": slug,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                continue
            record["query_url"] = url
            accepted.append(record)

    accepted.sort(key=lambda row: (int(row["start_ts"]), str(row["slug"])))
    rejected.sort(key=lambda row: row["slug"])
    return accepted, rejected


def _manifest(
    *,
    identities: list[dict[str, object]],
    accepted: list[dict[str, object]],
    rejected: list[dict[str, str]],
    predictions_path: Path | None,
    output_path: Path,
) -> dict[str, object]:
    collector_path = Path(__file__).resolve()
    return {
        "contract": "senex-order098-polymarket-5m-resolution-corpus-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": SOURCE,
        "gamma_base": GAMMA_BASE,
        "collector_file_sha256": _file_sha256(collector_path),
        "predictions_file_sha256": (
            _file_sha256(predictions_path) if predictions_path is not None else None
        ),
        "requested_markets": len(identities),
        "accepted_markets": len(accepted),
        "rejected_markets": len(rejected),
        "resolution_records_sha256": _sha256_text(_canonical_json(accepted)),
        "output_file_sha256": _file_sha256(output_path),
        "queries": [
            {
                "slug": str(item["slug"]),
                "condition_id": item.get("condition_id"),
            }
            for item in identities
        ],
        "rejections": rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an auditable Polymarket BTC 5m resolution corpus."
    )
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    identities: list[dict[str, object]] = []
    if args.predictions is not None:
        identities.extend(
            extract_market_identities(read_jsonl(args.predictions))
        )
    identities.extend(_slug_identity(slug) for slug in args.slug)
    identities = _merge_identities(identities)
    if not identities:
        parser.error("no exact BTC 5m market identities were supplied")

    accepted, rejected = collect_resolutions(
        identities,
        timeout_s=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, accepted)
    manifest = _manifest(
        identities=identities,
        accepted=accepted,
        rejected=rejected,
        predictions_path=args.predictions,
        output_path=args.output,
    )
    args.manifest.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "PASS" if not rejected else "BLOCKED_PARTIAL_CORPUS",
        "requested": len(identities),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "output": str(args.output),
        "manifest": str(args.manifest),
    }, sort_keys=True))
    return 0 if not rejected else 2


if __name__ == "__main__":
    raise SystemExit(main())
