from __future__ import annotations

import json

from senecio_polymarket.backend.gptrader.schemas import OUTCOME_FUTURE_KEYS
from senecio_polymarket.backend.gptrader.verdict import (
    Verdict,
    block_bootstrap_mean_ci,
    evaluate_verdict,
    persist_verdict,
    wilson_interval,
)


COMMON = {
    "independent_1h": 600,
    "calendar_days": 25,
    "cost_stress_2x_sign_stable": True,
}


def verdict(**overrides) -> Verdict:
    metrics = {**COMMON, **overrides}
    return evaluate_verdict(metrics).verdict


def test_exact_verdict_enum() -> None:
    assert {item.value for item in Verdict} == {
        "SENEX_SIGNAL_USEFUL_EVIDENCE",
        "SENEX_SIGNAL_NOT_USEFUL",
        "SENEX_DIRECTION_ANTI_INFORMATIVE",
        "GPTRADER_POLICY_BAD_OR_UNPROVEN",
        "EXECUTION_ASSUMPTIONS_DOMINATE",
        "INCREMENTAL_EDGE_NOT_ESTABLISHED",
        "INSUFFICIENT_DATA",
        "INDETERMINATE",
    }


def test_sample_gate_precedes_all_other_claims() -> None:
    assert verdict(independent_1h=599, signal_evidence=True) is Verdict.INSUFFICIENT_DATA
    assert verdict(independent_1h=312, calendar_days=13, signal_evidence=True) is Verdict.INSUFFICIENT_DATA


def test_every_preregistered_verdict_path() -> None:
    assert verdict(cost_stress_2x_sign_stable=False) is Verdict.EXECUTION_ASSUMPTIONS_DOMINATE
    assert verdict(
        direction_effect_below_neutral=True,
        dependence_aware_direction_robust=True,
    ) is Verdict.SENEX_DIRECTION_ANTI_INFORMATIVE
    assert verdict(
        direction_neutral=True,
        signal_evidence=False,
        fixed_risk_beats_abstain=False,
    ) is Verdict.SENEX_SIGNAL_NOT_USEFUL
    assert verdict(
        signal_evidence=True,
        incremental_established=True,
        policy_evaluated=True,
        policy_improves_fixed_risk=False,
    ) is Verdict.GPTRADER_POLICY_BAD_OR_UNPROVEN
    assert verdict(
        signal_evidence=True,
        incremental_established=False,
        policy_evaluated=False,
    ) is Verdict.INCREMENTAL_EDGE_NOT_ESTABLISHED
    assert verdict(
        signal_evidence=True,
        incremental_established=True,
        fixed_risk_beats_abstain=True,
        dependence_aware_ci_excludes_zero=True,
    ) is Verdict.SENEX_SIGNAL_USEFUL_EVIDENCE
    assert verdict() is Verdict.INDETERMINATE


def test_positive_pnl_or_wilson_alone_cannot_emit_useful_evidence() -> None:
    result = verdict(
        positive_pnl_usd=999.0,
        wilson_descriptive_excludes_neutral=True,
    )
    assert result is Verdict.INDETERMINATE


def test_anti_informative_never_implies_flip() -> None:
    result = evaluate_verdict({
        **COMMON,
        "direction_effect_below_neutral": True,
        "dependence_aware_direction_robust": True,
    })
    assert result.verdict is Verdict.SENEX_DIRECTION_ANTI_INFORMATIVE
    assert "FLIP" not in json.dumps(result.as_dict(), sort_keys=True)


def test_block_bootstrap_is_seeded_dependence_aware_and_deterministic() -> None:
    values = [1.0] * 24
    first = block_bootstrap_mean_ci(values, block_size=6, resamples=400, seed=86)
    second = block_bootstrap_mean_ci(values, block_size=6, resamples=400, seed=86)
    assert first == second
    assert first["block_size"] == 6
    assert first["temporal_dependence_acknowledged"] is True
    assert first["lower"] == first["upper"] == 1.0


def test_wilson_is_explicitly_descriptive_only() -> None:
    interval = wilson_interval(60, 100)
    assert 0.0 <= interval["lower"] < interval["upper"] <= 1.0
    assert interval["descriptive_only"] is True


def test_persisted_verdict_contains_gate_and_provenance(tmp_path) -> None:
    result = persist_verdict(
        tmp_path,
        {
            **COMMON,
            "signal_evidence": True,
            "incremental_established": True,
            "fixed_risk_beats_abstain": True,
            "dependence_aware_ci_excludes_zero": True,
        },
        provenance={"head_sha": "abc123", "method": "block-bootstrap"},
    )
    payload = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert result.verdict is Verdict.SENEX_SIGNAL_USEFUL_EVIDENCE
    assert payload["verdict"] == "SENEX_SIGNAL_USEFUL_EVIDENCE"
    assert payload["independent_1h"] == 600
    assert payload["calendar_days"] == 25
    assert payload["provenance"]["head_sha"] == "abc123"


def test_known_settlement_and_future_aliases_remain_denied() -> None:
    required = {
        "outcome",
        "outcome_15m",
        "outcome_1h",
        "outcomes_dual",
        "price_15m_later",
        "price_1h_later",
        "future_price",
        "post_t0_price",
        "settled_at",
        "settlement",
        "settlement_proof",
        "settlement_proof_v1",
        "settlement_cas",
        "resolution_price",
        "resolved_at",
        "realized_pnl",
        "realized_pnl_usd",
        "review_payload",
    }
    assert required <= OUTCOME_FUTURE_KEYS
