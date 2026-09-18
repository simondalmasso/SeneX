from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpProbAudit:
    formula: str
    down_relation: str
    raw_semantics: str
    calibrated_probability: bool
    prospectively_calibratable: bool
    retroactive_calibration_allowed: bool
    brier_logloss_allowed_now: bool
    recommended_mapping: str
    required_validation: str
    target_semantics_status: str
    stationarity_requirements: tuple[str, ...]


def audit_up_prob_semantics() -> UpProbAudit:
    return UpProbAudit(
        formula="sigmoid(5 * total_pressure)",
        down_relation="down_prob = 1 - up_prob",
        raw_semantics="LOGISTIC_SQUASHED_ENGINEERED_PRESSURE_SCORE",
        calibrated_probability=False,
        prospectively_calibratable=True,
        retroactive_calibration_allowed=False,
        brier_logloss_allowed_now=False,
        recommended_mapping="PLATT_ON_LOGIT_UP_PROB",
        required_validation="DISJOINT_FORWARD_VALIDATION_COHORT",
        target_semantics_status="MUST_BE_PREDECLARED_BEFORE_CALIBRATION_COHORT",
        stationarity_requirements=(
            "FIXED_CODE_HASH",
            "FIXED_CONFIG_HASH",
            "FIXED_EFFECTIVE_WEIGHTS_HASH",
            "FIXED_FEATURE_AVAILABILITY_POLICY",
            "INDEPENDENT_NONOVERLAP_1H_ROWS",
            "FUTURE_ROWS_ONLY_AFTER_REGISTRATION",
        ),
    )
