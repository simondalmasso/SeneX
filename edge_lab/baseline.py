from __future__ import annotations

from dataclasses import dataclass

from .metrics import brier_score, log_loss


VALID_PROBABILITY_SEMANTICS = frozenset({"CALIBRATED_PROBABILITY"})


@dataclass(frozen=True)
class ProbabilityObservation:
    p_market: float
    p_senex: float
    outcome: int
    market_horizon: str
    senex_horizon: str
    p_senex_semantics: str
    resolved: bool

    def __post_init__(self) -> None:
        for name, value in (("p_market", self.p_market), ("p_senex", self.p_senex)):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if int(self.outcome) not in (0, 1):
            raise ValueError("outcome must be 0 or 1")


@dataclass(frozen=True)
class BaselineComparison:
    verdict: str
    n: int
    brier_market: float | None
    brier_senex: float | None
    delta_brier: float | None
    logloss_market: float | None
    logloss_senex: float | None
    delta_logloss: float | None
    failure_reasons: tuple[str, ...]


def compare_probability_baseline(
    observations: list[ProbabilityObservation],
) -> BaselineComparison:
    reasons: list[str] = []
    if not observations:
        reasons.append("NO_OBSERVATIONS")
    if any(row.market_horizon != row.senex_horizon for row in observations):
        reasons.append("HORIZON_MISMATCH")
    if any(row.p_senex_semantics not in VALID_PROBABILITY_SEMANTICS for row in observations):
        reasons.append("P_SENEX_SEMANTICS_UNVALIDATED")
    if any(not row.resolved for row in observations):
        reasons.append("UNRESOLVED_OUTCOME")

    if reasons:
        return BaselineComparison(
            verdict="INCONCLUSIVE",
            n=len(observations),
            brier_market=None,
            brier_senex=None,
            delta_brier=None,
            logloss_market=None,
            logloss_senex=None,
            delta_logloss=None,
            failure_reasons=tuple(dict.fromkeys(reasons)),
        )

    outcomes = [row.outcome for row in observations]
    p_market = [row.p_market for row in observations]
    p_senex = [row.p_senex for row in observations]
    bm = brier_score(p_market, outcomes)
    bs = brier_score(p_senex, outcomes)
    lm = log_loss(p_market, outcomes)
    ls = log_loss(p_senex, outcomes)

    # A score delta alone is not an EDGE claim. The minimal v1 harness does
    # not estimate uncertainty/stability, so it fails closed even with scores.
    reasons.append("INSUFFICIENT_SAMPLE_FOR_EDGE_CLAIM" if len(observations) < 30 else "UNCERTAINTY_NOT_ESTIMATED")
    return BaselineComparison(
        verdict="INCONCLUSIVE",
        n=len(observations),
        brier_market=bm,
        brier_senex=bs,
        delta_brier=bs - bm,
        logloss_market=lm,
        logloss_senex=ls,
        delta_logloss=ls - lm,
        failure_reasons=tuple(reasons),
    )
