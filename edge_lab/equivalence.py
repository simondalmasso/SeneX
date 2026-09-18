from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class PolymarketHourlyContract:
    asset: str
    start_ts: str
    end_ts: str
    timezone: str
    resolution: str
    data_source: str
    tie_semantics: str
    condition_id: str | None = None
    slug: str | None = None


@dataclass(frozen=True)
class SenexHourlyContract:
    asset: str
    start_ts: str
    end_ts: str
    timezone: str
    resolution: str
    data_source: str
    tie_semantics: str


@dataclass(frozen=True)
class EquivalenceResult:
    verdict: str
    reasons: tuple[str, ...]
    directional_baseline_allowed: bool


def compare_exact_hourly_equivalence(
    polymarket: PolymarketHourlyContract,
    senex: SenexHourlyContract,
) -> EquivalenceResult:
    reasons: list[str] = []
    if polymarket.asset.upper() != senex.asset.upper():
        reasons.append("ASSET_MISMATCH")
    if _instant(polymarket.start_ts) != _instant(senex.start_ts):
        reasons.append("START_TIMESTAMP_MISMATCH")
    if _instant(polymarket.end_ts) != _instant(senex.end_ts):
        reasons.append("END_TIMESTAMP_MISMATCH")
    if polymarket.resolution != senex.resolution:
        reasons.append("RESOLUTION_SEMANTICS_MISMATCH")
    if polymarket.data_source.strip().lower() != senex.data_source.strip().lower():
        reasons.append("DATA_SOURCE_MISMATCH")
    if polymarket.tie_semantics != senex.tie_semantics:
        reasons.append("TIE_SEMANTICS_MISMATCH")

    equivalent = not reasons
    return EquivalenceResult(
        verdict="EQUIVALENT" if equivalent else "NOT_EQUIVALENT",
        reasons=tuple(reasons),
        directional_baseline_allowed=equivalent,
    )
