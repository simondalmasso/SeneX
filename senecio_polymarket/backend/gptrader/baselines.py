from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any

DEFAULT_CONFIDENCE_THRESHOLD = 0.60
RAW_SCORE_SEMANTICS = "UNCALIBRATED_SCORE"


class BaselinePolicy(str, Enum):
    ALWAYS_ABSTAIN = "ALWAYS_ABSTAIN"
    FOLLOW_ALL_DIRECTIONAL = "FOLLOW_ALL_DIRECTIONAL"
    FIXED_CONFIDENCE_THRESHOLD = "FIXED_CONFIDENCE_THRESHOLD"
    EV_SIGN = "EV_SIGN"


@dataclass(frozen=True)
class PolicyDecision:
    policy_id: str
    packet_id: str
    symbol: str
    decision_ts: str
    action: str
    senex_direction: str
    confidence: float | None
    ev: float | None
    up_prob: float | None
    eligibility: bool
    reason_codes: tuple[str, ...]
    score_semantics: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "packet_id": self.packet_id,
            "symbol": self.symbol,
            "decision_ts": self.decision_ts,
            "action": self.action,
            "senex_direction": self.senex_direction,
            "confidence": self.confidence,
            "ev": self.ev,
            "up_prob": self.up_prob,
            "eligibility": self.eligibility,
            "reason_codes": list(self.reason_codes),
            "score_semantics": copy.deepcopy(self.score_semantics),
        }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _up_prob(packet: dict[str, Any]) -> float | None:
    audit = packet.get("_audit") if isinstance(packet.get("_audit"), dict) else {}
    pipeline = audit.get("pipeline") if isinstance(audit.get("pipeline"), dict) else {}
    step2 = pipeline.get("step2_features") if isinstance(pipeline.get("step2_features"), dict) else {}
    return _finite_number(step2.get("up_prob"))


def evaluate_baseline(
    packet: dict[str, Any],
    policy: BaselinePolicy | str,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> PolicyDecision:
    if not isinstance(packet, dict):
        raise TypeError("packet must be a dict")
    policy = BaselinePolicy(policy)
    threshold = _finite_number(confidence_threshold)
    if threshold is None:
        raise ValueError("confidence_threshold must be finite")

    packet_id = str(packet.get("packet_id") or "").strip()
    if not packet_id:
        raise ValueError("packet_id is required")
    timestamp = str(packet.get("timestamp") or "").strip()
    if not timestamp:
        raise ValueError("timestamp is required")

    direction = str(packet.get("prediction") or "").upper().strip()
    directional = direction in {"LONG", "SHORT"}
    confidence = _finite_number(packet.get("confidence"))
    ev = _finite_number(packet.get("ev"))
    up_prob = _up_prob(packet)

    action = "ABSTAIN"
    reasons: tuple[str, ...]
    if not directional:
        reasons = ("NON_DIRECTIONAL_PACKET",)
    elif policy is BaselinePolicy.ALWAYS_ABSTAIN:
        reasons = ("BASELINE_ALWAYS_ABSTAIN",)
    elif policy is BaselinePolicy.FOLLOW_ALL_DIRECTIONAL:
        action = "TAKE"
        reasons = ("FOLLOW_SENEX_DIRECTION",)
    elif policy is BaselinePolicy.FIXED_CONFIDENCE_THRESHOLD:
        if confidence is not None and confidence >= threshold:
            action = "TAKE"
            reasons = ("CONFIDENCE_AT_OR_ABOVE_THRESHOLD",)
        else:
            reasons = ("CONFIDENCE_BELOW_THRESHOLD",)
    elif policy is BaselinePolicy.EV_SIGN:
        if ev is not None and ev > 0.0:
            action = "TAKE"
            reasons = ("EV_SCORE_POSITIVE",)
        else:
            reasons = ("EV_SCORE_NOT_POSITIVE",)
    else:
        raise AssertionError("unreachable policy")

    return PolicyDecision(
        policy_id=policy.value,
        packet_id=packet_id,
        symbol=str(packet.get("symbol") or ""),
        decision_ts=timestamp,
        action=action,
        senex_direction=direction,
        confidence=confidence,
        ev=ev,
        up_prob=up_prob,
        eligibility=directional,
        reason_codes=reasons,
        score_semantics={
            "confidence": RAW_SCORE_SEMANTICS,
            "up_prob": RAW_SCORE_SEMANTICS,
            "ev": "SENEX_UNCALIBRATED_SCORE",
        },
    )
