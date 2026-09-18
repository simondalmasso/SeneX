import math

from edge_lab.baseline import ProbabilityObservation, compare_probability_baseline


def _obs(**overrides):
    values = {
        "p_market": 0.61,
        "p_senex": 0.57,
        "outcome": 1,
        "market_horizon": "1h",
        "senex_horizon": "1h",
        "p_senex_semantics": "CALIBRATED_PROBABILITY",
        "resolved": True,
    }
    values.update(overrides)
    return ProbabilityObservation(**values)


def test_hyp001_rejects_horizon_mismatch_before_scoring():
    result = compare_probability_baseline([_obs(market_horizon="5m")])
    assert result.verdict == "INCONCLUSIVE"
    assert result.brier_market is None
    assert result.brier_senex is None
    assert "HORIZON_MISMATCH" in result.failure_reasons


def test_hyp001_rejects_unvalidated_senex_probability_semantics():
    result = compare_probability_baseline([
        _obs(p_senex_semantics="UNVALIDATED_MODEL_UP_PROB")
    ])
    assert result.verdict == "INCONCLUSIVE"
    assert "P_SENEX_SEMANTICS_UNVALIDATED" in result.failure_reasons


def test_hyp001_rejects_unresolved_observations():
    result = compare_probability_baseline([_obs(resolved=False)])
    assert result.verdict == "INCONCLUSIVE"
    assert "UNRESOLVED_OUTCOME" in result.failure_reasons


def test_hyp001_scores_valid_same_horizon_probabilities():
    rows = [
        _obs(p_market=0.60, p_senex=0.70, outcome=1),
        _obs(p_market=0.55, p_senex=0.40, outcome=0),
    ]
    result = compare_probability_baseline(rows)
    assert result.verdict == "INCONCLUSIVE"
    assert result.n == 2
    assert math.isclose(result.brier_market, (0.16 + 0.3025) / 2)
    assert math.isclose(result.brier_senex, (0.09 + 0.16) / 2)
    assert result.delta_brier == result.brier_senex - result.brier_market
    assert result.logloss_market is not None
    assert result.logloss_senex is not None
    assert "INSUFFICIENT_SAMPLE_FOR_EDGE_CLAIM" in result.failure_reasons


def test_probability_domain_is_validated():
    try:
        _obs(p_market=1.1)
    except ValueError as exc:
        assert "p_market" in str(exc)
    else:
        raise AssertionError("expected invalid probability to fail")
