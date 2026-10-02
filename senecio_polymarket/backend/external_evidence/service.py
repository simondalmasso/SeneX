from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .journal import AppendResult, ExternalEvidenceJournal
from .paths import ExternalEvidencePaths
from .schema import EvidenceCapture
from .security import validate_public_url


class Collector(Protocol):
    def collect(self, url: str, *, timeout: float = 15.0) -> EvidenceCapture: ...


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ShadowEvidenceService:
    """Research-only persistence boundary.

    There is intentionally no method that returns a trading signal, prediction,
    threshold, direction, action, order, or sizing decision.
    """

    def __init__(self, paths: ExternalEvidencePaths | None = None):
        self.paths = paths or ExternalEvidencePaths.default()
        self.journal = ExternalEvidenceJournal(self.paths)

    def persist(
        self,
        capture: EvidenceCapture,
        *,
        captured_at: str | None = None,
    ) -> AppendResult:
        normalized_url = validate_public_url(capture.source_url)
        if normalized_url != capture.source_url:
            capture = EvidenceCapture(
                provider=capture.provider,
                collector=capture.collector,
                source_kind=capture.source_kind,
                source_url=normalized_url,
                native_id=capture.native_id,
                published_at=capture.published_at,
                observed_at=capture.observed_at,
                raw=capture.raw,
                content=capture.content,
                provider_version=capture.provider_version,
                metadata=capture.metadata,
            )
        event = capture.to_event(captured_at=captured_at or _utcnow())
        if not event.shadow_only or event.decision_allowed or event.t0_allowed:
            raise RuntimeError("external evidence must remain shadow-only")
        return self.journal.append(event, raw=bytes(capture.raw))

    def collect_and_persist(
        self,
        collector: Collector,
        url: str,
        *,
        timeout: float = 15.0,
    ) -> AppendResult:
        capture = collector.collect(validate_public_url(url), timeout=float(timeout))
        return self.persist(capture)
