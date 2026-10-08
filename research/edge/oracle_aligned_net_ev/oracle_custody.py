"""M17 research-only oracle checks. Matched bytes are NOT an attested Chainlink label.

No network, trading, wallet, or production imports. The source rule MUST be
fetched and preserved as original market-specific bytes before any promotion.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation


class CustodyError(ValueError):
    """Missing, inconsistent, or non-original research evidence."""


def sha256_bytes(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise CustodyError("original source must be bytes")
    return hashlib.sha256(data).hexdigest()


def _number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CustodyError("invalid oracle value") from exc
    if not result.is_finite():
        raise CustodyError("nonfinite oracle value")
    return result


def resolution_side(initial: object, final: object, rule: dict) -> str:
    """Apply *provided* tested rule; does not authenticate its real-market source."""
    if not isinstance(rule, dict):
        raise CustodyError("missing market rule")
    if (rule.get("source") != "chainlink-btc-usd-twap-60s"
            or rule.get("tie_winner") != "UP"):
        raise CustodyError("unverified oracle source/tie rule")
    start, end = _number(initial), _number(final)
    if start <= 0 or end <= 0:
        raise CustodyError("nonpositive oracle price")
    return "UP" if end >= start else "DOWN"


def validate_receipt_linkage(
    t0: dict, t1: dict, t0_raw: bytes, t1_raw: bytes, market_rule_raw: bytes
) -> dict:
    """Reject lineage mismatches; NEVER claim scientific label verification.

    Byte equality merely shows self-consistent blobs, not original acquisition
    timestamps, feed signature, platform finality, or independent attestation.
    """
    if not isinstance(t0, dict) or not isinstance(t1, dict):
        raise CustodyError("missing typed receipts")
    for raw, expected in (
        (t0_raw, t0.get("raw_bytes_sha256")),
        (t1_raw, t1.get("raw_label_bytes_sha256")),
        (market_rule_raw, t0.get("original_market_rules_bytes_sha256")),
        (market_rule_raw, t1.get("original_market_rules_bytes_sha256")),
    ):
        if not isinstance(expected, str) or sha256_bytes(raw) != expected:
            raise CustodyError("source bytes hash mismatch")
    if not t0_raw or not t1_raw or not market_rule_raw:
        raise CustodyError("empty source bytes")
    for field in ("market_id", "condition_id", "start_ms", "end_ms", "oracle_source_id"):
        if not t0.get(field) or t0.get(field) != t1.get(field):
            raise CustodyError(f"mismatched {field}")
    if t1.get("token_id") not in (t0.get("token_id_up"), t0.get("token_id_down")):
        raise CustodyError("T1 token not in T0 market")
    try:
        start = int(t0["start_ms"])
        end = int(t0["end_ms"])
        acquired = int(t0["received_at_ms"])
        labeled = int(t1["label_observed_at_ms"])
    except (TypeError, ValueError, KeyError) as exc:
        raise CustodyError("invalid acquisition/window timestamps") from exc
    if end - start != 300000 or not (start <= acquired < end) or labeled < end:
        raise CustodyError("future label or invalid 5m window")
    if t1.get("settlement_finality") != "final":
        raise CustodyError("non-final settlement")
    return {
        "custody": "MATCHED_BYTES_ONLY",
        "label_authority": "UNVERIFIED",
        "source_class": str(t1.get("source_class") or "UNKNOWN"),
        "n_real_verified": 0,
        "edge": "UNPROVEN",
    }
