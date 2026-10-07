from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

from .common import ChallengerContractError, _utc


CHALLENGER_ID = "RECENCY_CHALLENGER_V1"
EXTRACTOR_ID = "SENEX_RECENCY_NORMALIZER_V1"
LOOKBACK_1H_SECONDS = 3600
LOOKBACK_6H_SECONDS = 6 * 3600
ALLOWED_ZERO_SPEND_SOURCES = frozenset({"reddit", "hackernews", "github", "public_web"})

NORMALIZER_CONTRACT = {
    "id": EXTRACTOR_ID,
    "version": 1,
    "allowed_source_types": sorted(ALLOWED_ZERO_SPEND_SOURCES),
    "topics": ["crypto", "macro", "other"],
    "sentiment_range": [-1.0, 1.0],
    "capture_time_rule": "published_at<=captured_at<=cutoff_ts",
    "forbid_market_odds": True,
    "required_provenance": [
        "source",
        "document_id",
        "published_at",
        "captured_at",
        "content_sha256",
        "extractor_id",
        "extractor_sha256",
    ],
}
NORMALIZER_SHA256 = hashlib.sha256(
    json.dumps(
        NORMALIZER_CONTRACT,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
).hexdigest()

FEATURE_ORDER = (
    "document_count_6h",
    "document_count_1h",
    "source_diversity_6h",
    "source_concentration_6h",
    "sentiment_mean_6h",
    "sentiment_dispersion_6h",
    "sentiment_direction_balance_6h",
    "crypto_attention_fraction_6h",
    "macro_attention_fraction_6h",
    "breaking_event_flag_6h",
    "missing_all_sources",
)


@dataclass(frozen=True)
class RecencyDocument:
    source: str
    document_id: str
    published_at: str
    captured_at: str
    topic: str
    sentiment: float
    is_breaking: bool
    content_sha256: str
    extractor_id: str
    extractor_sha256: str
    contains_market_odds: bool = False


def _validate_document(document: RecencyDocument, cutoff_ts: str) -> None:
    source = document.source.strip().lower()
    if not source or not document.document_id.strip():
        raise ChallengerContractError("recency source/document_id is required")
    if source not in ALLOWED_ZERO_SPEND_SOURCES:
        raise ChallengerContractError(
            f"recency source is not in frozen zero-spend source set: {source}"
        )
    if document.contains_market_odds:
        raise ChallengerContractError(
            "RECENCY_CHALLENGER_V1 forbids Polymarket odds/market-price inputs"
        )
    published = _utc(document.published_at)
    captured = _utc(document.captured_at)
    cutoff = _utc(cutoff_ts)
    if published > cutoff or captured > cutoff:
        raise ChallengerContractError("post-cutoff recency evidence is forbidden")
    if captured < published:
        raise ChallengerContractError("captured_at cannot precede published_at")
    if document.topic not in {"crypto", "macro", "other"}:
        raise ChallengerContractError("recency topic must be crypto, macro, or other")
    try:
        sentiment = float(document.sentiment)
    except (TypeError, ValueError) as exc:
        raise ChallengerContractError("sentiment must be numeric") from exc
    if not math.isfinite(sentiment) or not -1.0 <= sentiment <= 1.0:
        raise ChallengerContractError("sentiment must be finite in [-1,1]")
    digest = document.content_sha256.strip().lower()
    if len(digest) != 64:
        raise ChallengerContractError("content_sha256 must be 64 hex characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ChallengerContractError("content_sha256 is invalid") from exc

    if document.extractor_id != EXTRACTOR_ID:
        raise ChallengerContractError("recency extractor_id does not match frozen contract")
    extractor_digest = document.extractor_sha256.strip().lower()
    if len(extractor_digest) != 64:
        raise ChallengerContractError("extractor_sha256 must be 64 hex characters")
    try:
        int(extractor_digest, 16)
    except ValueError as exc:
        raise ChallengerContractError("extractor_sha256 is invalid") from exc
    if extractor_digest != NORMALIZER_SHA256:
        raise ChallengerContractError(
            "extractor_sha256 does not match frozen SENEX_RECENCY_NORMALIZER_V1"
        )

    if type(document.is_breaking) is not bool or type(document.contains_market_odds) is not bool:
        raise ChallengerContractError("recency boolean fields must be bool")


def aggregate_features(
    documents: Iterable[RecencyDocument],
    *,
    cutoff_ts: str,
) -> dict[str, float]:
    """Aggregate only causal public-information evidence available at cutoff."""
    cutoff = _utc(cutoff_ts)
    docs = list(documents)
    identities: set[tuple[str, str]] = set()
    extractor_hashes: set[str] = set()
    for document in docs:
        _validate_document(document, cutoff_ts)
        identity = (document.source.strip().lower(), document.document_id.strip())
        if identity in identities:
            raise ChallengerContractError(
                f"duplicate recency document identity: {identity[0]}:{identity[1]}"
            )
        identities.add(identity)
        extractor_hashes.add(document.extractor_sha256.strip().lower())

    if len(extractor_hashes) > 1:
        raise ChallengerContractError(
            "recency batch mixes extractor hashes; one frozen extractor is required"
        )

    six_hour_floor = cutoff - timedelta(seconds=LOOKBACK_6H_SECONDS)
    one_hour_floor = cutoff - timedelta(seconds=LOOKBACK_1H_SECONDS)
    recent = [doc for doc in docs if _utc(doc.published_at) >= six_hour_floor]
    last_hour = [doc for doc in recent if _utc(doc.published_at) >= one_hour_floor]

    if not recent:
        return {
            "document_count_6h": 0.0,
            "document_count_1h": 0.0,
            "source_diversity_6h": 0.0,
            "source_concentration_6h": 0.0,
            "sentiment_mean_6h": 0.0,
            "sentiment_dispersion_6h": 0.0,
            "sentiment_direction_balance_6h": 0.0,
            "crypto_attention_fraction_6h": 0.0,
            "macro_attention_fraction_6h": 0.0,
            "breaking_event_flag_6h": 0.0,
            "missing_all_sources": 1.0,
        }

    n = len(recent)
    sentiments = [float(doc.sentiment) for doc in recent]
    mean = sum(sentiments) / n
    variance = sum((value - mean) ** 2 for value in sentiments) / n
    direction = sum(
        1.0 if value > 0 else -1.0 if value < 0 else 0.0
        for value in sentiments
    ) / n
    counts = Counter(doc.source.strip().lower() for doc in recent)
    concentration = sum((count / n) ** 2 for count in counts.values())

    return {
        "document_count_6h": float(n),
        "document_count_1h": float(len(last_hour)),
        "source_diversity_6h": float(len(counts)),
        "source_concentration_6h": float(concentration),
        "sentiment_mean_6h": float(mean),
        "sentiment_dispersion_6h": float(math.sqrt(variance)),
        "sentiment_direction_balance_6h": float(direction),
        "crypto_attention_fraction_6h": float(
            sum(doc.topic == "crypto" for doc in recent) / n
        ),
        "macro_attention_fraction_6h": float(
            sum(doc.topic == "macro" for doc in recent) / n
        ),
        "breaking_event_flag_6h": float(any(doc.is_breaking for doc in recent)),
        "missing_all_sources": 0.0,
    }


def feature_vector(features: dict[str, float]) -> list[float]:
    if set(features) != set(FEATURE_ORDER):
        missing = sorted(set(FEATURE_ORDER) - set(features))
        extra = sorted(set(features) - set(FEATURE_ORDER))
        raise ChallengerContractError(
            f"recency feature schema mismatch missing={missing} extra={extra}"
        )
    values = [float(features[name]) for name in FEATURE_ORDER]
    if not all(math.isfinite(value) for value in values):
        raise ChallengerContractError("recency features must all be finite")
    return values
