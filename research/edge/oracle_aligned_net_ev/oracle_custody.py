"""M17 research-only oracle checks. Matched bytes are NOT an attested Chainlink label.

No network, trading, wallet, or production imports. The source rule MUST be
fetched and preserved as original market-specific bytes before any promotion.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    t0: dict,
    t1: dict,
    t0_raw: bytes,
    t1_raw: bytes,
    market_rule_raw: bytes,
    *,
    t0_receipt_raw: bytes,
) -> dict:
    """Check exact-byte and identity *consistency*, NEVER independent oracle authority.

    t0_raw is the source message observed at T0; t0_receipt_raw is the
    serialized original T0 receipt, distinctly bound by T1. Both are required.
    Original publication timing/signatures cannot be proven by these checks.
    """
    if not isinstance(t0, dict) or not isinstance(t1, dict):
        raise CustodyError("missing typed receipts")
    for raw in (t0_raw, t1_raw, market_rule_raw, t0_receipt_raw):
        if not isinstance(raw, bytes) or not raw:
            raise CustodyError("original byte evidence missing")
    try:
        parsed_t0 = json.loads(
            t0_receipt_raw.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        raise CustodyError("invalid raw T0 receipt JSON") from exc
    if parsed_t0 != t0:
        raise CustodyError("T0 receipt bytes do not describe supplied T0 object")
    for raw, expected in (
        (t0_raw, t0.get("raw_bytes_sha256")),
        (t1_raw, t1.get("raw_label_bytes_sha256")),
        (market_rule_raw, t0.get("original_market_rules_bytes_sha256")),
        (market_rule_raw, t1.get("original_market_rules_bytes_sha256")),
        (t0_receipt_raw, t1.get("T0_receipt_sha256")),
    ):
        if not isinstance(expected, str) or sha256_bytes(raw) != expected:
            raise CustodyError("source/receipt bytes hash mismatch")
    rule_hash_t0 = t0.get("rule_version_sha256")
    rule_hash_t1 = t1.get("rule_version_sha256")
    if (
        not isinstance(rule_hash_t0, str)
        or not re.fullmatch(r"[a-f0-9]{64}", rule_hash_t0)
        or rule_hash_t0 != rule_hash_t1
    ):
        raise CustodyError("market rule version mismatch")
    for field in ("market_id", "condition_id", "start_ms", "end_ms"):
        if t0.get(field) is None or t0.get(field) != t1.get(field):
            raise CustodyError(f"mismatched {field}")
    if (
        t0.get("oracle_source_id") != "chainlink-btc-usd-twap-60s"
        or t1.get("exact_oracle_source") != t0.get("oracle_source_id")
    ):
        raise CustodyError("incorrect or mismatched exact oracle source")
    up, down = t0.get("token_id_yes"), t0.get("token_id_no")
    if not isinstance(up, str) or not isinstance(down, str) or not up or not down or up == down:
        raise CustodyError("invalid two-token market contract")
    if t1.get("token_id") not in (up, down):
        raise CustodyError("T1 token not in T0 market")
    for field in ("start_ms", "end_ms", "time_of_observation_ms", "received_at_ms", "clock_skew_bound_ms"):
        if type(t0.get(field)) is not int:
            raise CustodyError(f"invalid {field}")
    if type(t1.get("label_observed_at_ms")) is not int:
        raise CustodyError("invalid terminal observation timestamp")
    start, end = t0["start_ms"], t0["end_ms"]
    event, acquired = t0["time_of_observation_ms"], t0["received_at_ms"]
    skew = t0["clock_skew_bound_ms"]
    if (
        end - start != 300000
        or start % 300000 != 0
        or not (start <= event <= acquired < end)
        or skew < 0
        or acquired - event > skew
        or t1["label_observed_at_ms"] < end
    ):
        raise CustodyError("window, clock skew or label causality violation")
    if t1.get("settlement_finality") != "final":
        raise CustodyError("non-final settlement")
    flags = t0.get("validity_flags")
    if not isinstance(flags, list) or not all(isinstance(x, str) for x in flags):
        raise CustodyError("missing typed attrition flags")
    prediction = t0.get("prediction_id")
    score = t0.get("frozen_senex_score")
    if prediction is None:
        if score is not None or t0.get("side_candidate") != "ABSTAIN" or "NO_T0_SENEX_SIGNAL" not in flags:
            raise CustodyError("unmarked missing T0 model signal")
    elif not isinstance(prediction, str) or not prediction or score is None or not _number(score).is_finite():
        raise CustodyError("invalid available T0 model signal")
    if t0.get("book_snapshot_sha256") is None and "NO_BOOK" not in flags:
        raise CustodyError("unmarked missing T0 orderbook")
    return {
        "custody": "MATCHED_BYTES_ONLY",
        "label_authority": "UNVERIFIED",
        "source_class": str(t1.get("source_class") or "UNKNOWN"),
        "n_real_verified": 0,
        "edge": "UNPROVEN",
    }
