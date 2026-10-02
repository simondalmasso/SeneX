from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

MAX_CONTENT_BYTES = 256_000
MAX_RAW_BYTES = 1_000_000
MAX_METADATA_BYTES = 16_384
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)

_SENSITIVE_PARTS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
)


class EvidenceValidationError(ValueError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_time(value: str, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise EvidenceValidationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceValidationError(f"{field} must be RFC3339/ISO8601") from exc
    if parsed.tzinfo is None:
        raise EvidenceValidationError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _norm_time(value: str, field: str) -> str:
    parsed = _parse_time(value, field)
    return parsed.isoformat().replace("+00:00", "Z")


def _reject_sensitive_keys(value: Any, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_PARTS):
                raise EvidenceValidationError(f"sensitive metadata key forbidden: {path}.{key}")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def _safe_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = dict(value or {})
    _reject_sensitive_keys(metadata)
    try:
        encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("metadata must be JSON serializable") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise EvidenceValidationError("metadata exceeds bounded size")
    return metadata


@dataclass(frozen=True)
class ExternalEvidenceEvent:
    schema_version: str
    event_id: str
    provider: str
    collector: str
    provider_version: str
    source_kind: str
    source_url: str
    native_id: str | None
    published_at: str | None
    observed_at: str
    captured_at: str
    content: str
    content_sha256: str
    raw_sha256: str
    content_bytes: int
    raw_bytes: int
    metadata: dict[str, Any]
    shadow_only: bool = True
    decision_allowed: bool = False
    t0_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceCapture:
    provider: str
    collector: str
    source_kind: str
    source_url: str
    native_id: str | None
    published_at: str | None
    observed_at: str
    raw: bytes
    content: str
    provider_version: str
    metadata: Mapping[str, Any] | None = None

    def to_event(self, *, captured_at: str) -> ExternalEvidenceEvent:
        provider = str(self.provider or "").strip()
        collector = str(self.collector or "").strip()
        source_kind = str(self.source_kind or "").strip()
        source_url = str(self.source_url or "").strip()
        provider_version = str(self.provider_version or "").strip()
        if not provider or not collector or not source_kind or not source_url or not provider_version:
            raise EvidenceValidationError("provider/collector/source_kind/source_url/provider_version are required")
        if not isinstance(self.raw, (bytes, bytearray)):
            raise EvidenceValidationError("raw must be bytes")
        raw = bytes(self.raw)
        if len(raw) > MAX_RAW_BYTES:
            raise EvidenceValidationError("raw exceeds bounded size")
        content = str(self.content or "")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > MAX_CONTENT_BYTES:
            raise EvidenceValidationError("content exceeds bounded size")

        observed_dt = _parse_time(self.observed_at, "observed_at")
        captured_dt = _parse_time(captured_at, "captured_at")
        if observed_dt > captured_dt + CLOCK_SKEW_TOLERANCE:
            raise EvidenceValidationError("observed_at is after captured_at beyond clock tolerance")

        published_norm: str | None = None
        if self.published_at is not None:
            published_dt = _parse_time(self.published_at, "published_at")
            if published_dt > captured_dt + CLOCK_SKEW_TOLERANCE:
                raise EvidenceValidationError("published_at is after captured_at beyond clock tolerance")
            if published_dt > observed_dt + CLOCK_SKEW_TOLERANCE:
                raise EvidenceValidationError("published_at is after observed_at beyond clock tolerance")
            published_norm = published_dt.isoformat().replace("+00:00", "Z")

        raw_hash = _sha256(raw)
        content_hash = _sha256(content_bytes)
        native_id = str(self.native_id).strip() if self.native_id is not None else None
        identity = {
            "provider": provider,
            "source_url": source_url,
            "native_id": native_id,
            "published_at": published_norm,
            "raw_sha256": raw_hash,
            "content_sha256": content_hash,
        }
        event_id = _sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        return ExternalEvidenceEvent(
            schema_version="senex.external_evidence.v1",
            event_id=event_id,
            provider=provider,
            collector=collector,
            provider_version=provider_version,
            source_kind=source_kind,
            source_url=source_url,
            native_id=native_id,
            published_at=published_norm,
            observed_at=observed_dt.isoformat().replace("+00:00", "Z"),
            captured_at=captured_dt.isoformat().replace("+00:00", "Z"),
            content=content,
            content_sha256=content_hash,
            raw_sha256=raw_hash,
            content_bytes=len(content_bytes),
            raw_bytes=len(raw),
            metadata=_safe_metadata(self.metadata),
            shadow_only=True,
            decision_allowed=False,
            t0_allowed=False,
        )
