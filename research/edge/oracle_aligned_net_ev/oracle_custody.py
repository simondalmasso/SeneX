"""Research-only M17 custody checks: provenance integrity is not oracle authority."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation


class CustodyError(ValueError):
    """Reject malformed or unverifiable market-specific evidence."""


def sha256_bytes(data: bytes) -> str:
    if not isinstance(data, bytes):
        raise CustodyError("source must be exact original bytes")
    return hashlib.sha256(data).hexdigest()


def canonical_receipt_bytes(receipt: dict) -> bytes:
    """Deterministic SHA256 commitment of the ENTIRE T0 receipt JSON.

    This is a local serialization contract, NOT original external acquisition
    evidence. Every T0 field is covered; no caller-controlled 'verified' flags.
    """
    if not isinstance(receipt, dict) or not receipt:
        raise CustodyError("missing T0 receipt")
    try:
        return json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CustodyError("T0 receipt is not canonical JSON") from exc


def _number(value: object) -> Decimal:
    try:
        if isinstance(value, bool):
            raise TypeError("bool is not a price")
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CustodyError("invalid oracle value") from exc
    if not result.is_finite():
        raise CustodyError("nonfinite oracle value")
    return result


def resolution_side(initial: object, final: object, rule: dict) -> str:
    """Evaluate a *hypothetical verified rule*, never assert source authority."""
    if not isinstance(rule, dict) or (
        rule.get("source") != "chainlink-btc-usd-twap-60s"
        or rule.get("tie_winner") != "UP"
    ):
        raise CustodyError("missing/mismatched market-specific tie rule")
    a, b = _number(initial), _number(final)
    if min(a, b) <= 0:
        raise CustodyError("nonpositive oracle price")
    return "UP" if b >= a else "DOWN"


def classify_opportunity(t0: dict) -> dict:
    """Keep every original opportunity in denominator, including abstentions.

    Fails closed: returns explicit exclusions rather than imputing book/signal
    from later observations. Not an admission into a scientific cohort.
    """
    if not isinstance(t0, dict):
        raise CustodyError("T0 missing")
    flags = set(t0.get("validity_flags") or [])
    if not isinstance(t0.get("validity_flags"), list):
        raise CustodyError("flags must be a list")
    if t0.get("prediction_id") is None or t0.get("frozen_senex_score") is None:
        flags.add("NO_T0_SENEX_SIGNAL")
    if t0.get("best_ask") is None or t0.get("best_bid") is None or (
        t0.get("book_snapshot_sha256") is None
    ):
        flags.add("NO_BOOK")
    if not t0.get("market_fee_parameters") or t0.get("fee_parameters_sha256") is None:
        flags.add("UNVERIFIED_FEE")
    if flags and t0.get("side_candidate") != "ABSTAIN":
        raise CustodyError("excluded opportunity must abstain")
    return {
        "opportunity_count": 1, "eligible": not bool(flags),
        "excluded": bool(flags), "exclusion_reasons": sorted(flags),
        "portfolio_pnl_when_abstained": "0",
    }


def _strict_ms(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CustodyError("invalid " + field)
    return value


def validate_receipt_linkage(
    t0: dict, t1: dict, t0_raw: bytes, t1_raw: bytes, market_rule_raw: bytes,
    *, t0_receipt_raw: bytes
) -> dict:
    """Seal consistency across two receipt schemas; never promote to verified label.

    A same-digest check is self-consistency only: external signatures, original
    event acquisition and true settlement are intentionally NOT attested here.
    """
    if not isinstance(t0, dict) or not isinstance(t1, dict):
        raise CustodyError("missing receipt dictionaries")
    if not isinstance(t0_receipt_raw, bytes) or not t0_receipt_raw:
        raise CustodyError("original T0 receipt bytes missing")
    try:
        parsed=json.loads(t0_receipt_raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError,UnicodeError,TypeError) as exc:
        raise CustodyError("invalid original T0 receipt JSON") from exc
    if parsed != t0:
        raise CustodyError("original T0 receipt differs from T0 object")
    for raw, expected in (
        (t0_raw, t0.get("raw_bytes_sha256")),
        (t1_raw, t1.get("raw_label_bytes_sha256")),
        (market_rule_raw, t0.get("original_market_rules_bytes_sha256")),
        (market_rule_raw, t1.get("original_market_rules_bytes_sha256")),
        (market_rule_raw, t0.get("rule_version_sha256")),
        (market_rule_raw, t1.get("rule_version_sha256")),
        (t0_receipt_raw, t1.get("T0_receipt_sha256")),
    ):
        if not raw or not isinstance(expected, str) or sha256_bytes(raw) != expected:
            raise CustodyError("T0/T1/rule/version source digest mismatch")
    for key in ("market_id", "condition_id", "start_ms", "end_ms"):
        if t0.get(key) is None or t0.get(key) != t1.get(key):
            raise CustodyError("market/window mismatch: " + key)
    if not t0.get("market_id") or not t0.get("condition_id"):
        raise CustodyError("missing market identifier")
    if not t0.get("oracle_source_id") or (
        t0["oracle_source_id"] != t1.get("exact_oracle_source")
    ):
        raise CustodyError("exact oracle source mismatch")
    if t0.get("oracle_boundary_rule") != "UP_ON_EQUAL" or (
        t1.get("tie_handling") != t0.get("oracle_boundary_rule")
    ):
        raise CustodyError("tie/boundary contract mismatch")
    yes, no = t0.get("token_id_yes"), t0.get("token_id_no")
    if not yes or not no or yes == no or t1.get("token_id") not in (yes, no):
        raise CustodyError("T1 token not bound to exact T0 market")
    if t1.get("source_class") not in (
        "PLATFORM_TERMINAL_RESOLUTION", "PROVIDER_CHAINLINK_RELAY",
        "CHAINLINK_SIGNED_REPORT",
    ):
        raise CustodyError("unknown oracle source class")
    start = _strict_ms(t0.get("start_ms"), "start_ms")
    end = _strict_ms(t0.get("end_ms"), "end_ms")
    observed = _strict_ms(t0.get("time_of_observation_ms"), "observation timestamp")
    acquired = _strict_ms(t0.get("received_at_ms"), "T0 acquisition timestamp")
    labeled = _strict_ms(t1.get("label_observed_at_ms"), "T1 acquisition timestamp")
    skew = _strict_ms(t0.get("clock_skew_bound_ms"), "clock skew bound")
    if end - start != 300000 or start % 300000 != 0 or not (start <= observed <= acquired < end):
        raise CustodyError("invalid 5m timing or T0 lookahead")
    if acquired - observed > skew or labeled < end:
        raise CustodyError("clock skew bound or pre-terminal label")
    if t1.get("settlement_finality") != "final":
        raise CustodyError("settlement not final")
    # Validate abstentions, but NO recorded score/quotes can imply admitted edge.
    if t0.get("prediction_id") is None:
        if t0.get("frozen_senex_score") is not None or t0.get("score_provenance") != "NO_T0_SENEX_SIGNAL":
            raise CustodyError("missing signal must be explicit and unscored")
    elif (not isinstance(t0.get("prediction_id"),str)
          or not t0.get("prediction_id")
          or t0.get("frozen_senex_score") is None
          or t0.get("score_provenance") != "RAW_CONVICTION"):
        raise CustodyError("inconsistent RAW score provenance")
    attrition = classify_opportunity(t0)
    return {
        "custody": "MATCHED_BYTES_ONLY",
        "label_authority": "UNVERIFIED",
        "source_class": t1["source_class"],
        "n_real_verified": 0,
        "edge": "UNPROVEN",
        "eligible_quote": attrition["eligible"],
        "exclusion_reasons": attrition["exclusion_reasons"],
        "receipt_digest_sha256":sha256_bytes(t0_receipt_raw),
    }
