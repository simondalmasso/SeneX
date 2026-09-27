from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .store import logical_decision_rows

MIN_INDEPENDENT_1H = 600
MIN_CALENDAR_DAYS = 14
MAX_INDEPENDENT_1H_PER_DAY = 24


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def hour_cluster_id(timestamp: str) -> str:
    dt = _parse_timestamp(timestamp).replace(minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:00:00Z")


@dataclass(frozen=True)
class ResolvedSampleSummary:
    raw_resolved_rows: int
    independent_1h: int
    calendar_days: int
    cluster_ids: tuple[str, ...]


@dataclass(frozen=True)
class SampleGate:
    independent_1h: int
    calendar_days: int
    passed: bool
    verdict: str


def geometry_is_possible(independent_1h: int, calendar_days: int) -> bool:
    return independent_1h <= MAX_INDEPENDENT_1H_PER_DAY * calendar_days


def summarize_resolved_sample(rows: Iterable[dict[str, Any]]) -> ResolvedSampleSummary:
    clusters: set[str] = set()
    days: set[str] = set()
    raw = 0
    logical_rows = logical_decision_rows(
        [row for row in rows if isinstance(row, dict)]
    )
    for row in logical_rows:
        if row.get("resolved") is not True:
            continue
        raw += 1
        cluster = hour_cluster_id(str(row.get("timestamp") or ""))
        clusters.add(cluster)
        days.add(cluster[:10])
    ordered = tuple(sorted(clusters))
    summary = ResolvedSampleSummary(
        raw_resolved_rows=raw,
        independent_1h=len(ordered),
        calendar_days=len(days),
        cluster_ids=ordered,
    )
    if not geometry_is_possible(summary.independent_1h, summary.calendar_days):
        raise AssertionError("summarized sample violates hourly geometry")
    return summary


def sample_gate(independent_1h: int, calendar_days: int) -> SampleGate:
    if isinstance(independent_1h, bool) or not isinstance(independent_1h, int) or independent_1h < 0:
        raise ValueError("independent_1h must be a non-negative integer")
    if isinstance(calendar_days, bool) or not isinstance(calendar_days, int) or calendar_days < 0:
        raise ValueError("calendar_days must be a non-negative integer")
    if not geometry_is_possible(independent_1h, calendar_days):
        return SampleGate(
            independent_1h=independent_1h,
            calendar_days=calendar_days,
            passed=False,
            verdict="IMPOSSIBLE_SAMPLE_GEOMETRY",
        )
    passed = independent_1h >= MIN_INDEPENDENT_1H and calendar_days >= MIN_CALENDAR_DAYS
    return SampleGate(
        independent_1h=independent_1h,
        calendar_days=calendar_days,
        passed=passed,
        verdict="GATE_OPEN" if passed else "INSUFFICIENT_DATA",
    )
