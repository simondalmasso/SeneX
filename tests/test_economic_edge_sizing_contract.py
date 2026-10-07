[Reading 109 lines from start (total: 109 lines, 0 remaining)]

import pytest

from senecio_polymarket.backend.portfolio.portfolio_engine import (
    PortfolioEngine,
    PortfolioState,
)


def _state() -> PortfolioState:
    return PortfolioState(equity=10_000.0, cash=10_000.0)


def _prediction() -> dict:
    return {
        "id": "edge-contract-1",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "price_now": 100.0,
        "confidence": 0.90,
        "ev": 0.05,
        "_audit": {"regime_4h": "BULL", "spread_bps": 1.0},
    }


def _edge_contract(*, ci95_low_net_bps: float = 5.0) -> dict:
    return {
        "LONG": {
            "semantics": "VALIDATED_OOS_NET_EDGE",
            "target": "NET_RETURN_1H",
            "horizon_seconds": 3600,
            "economic_edge_net_bps": 20.0,
            "ci95_low_net_bps": ci95_low_net_bps,
            "execution_cost_bps": 15.0,
            "method": "PURGED_BLOCK_BOOTSTRAP_V1",
            "artifact_sha256": "a" * 64,
            "cost_model_sha256": "b" * 64,
        }
    }


def test_win_rate_above_50pct_cannot_authorize_sizing_without_validated_net_edge():
    engine = PortfolioEngine()

    proposal = engine.build_proposal(
        prediction=_prediction(),
        state=_state(),
        vol_pct=0.01,
        win_rate_by_direction={"LONG": 0.60},
    )

    assert proposal is None


def test_validated_positive_net_edge_authorizes_fixed_base_risk_not_kelly():
    engine = PortfolioEngine()

    proposal = engine.build_proposal(
        prediction=_prediction(),
        state=_state(),
        vol_pct=0.01,
        win_rate_by_direction={"LONG": 0.60},
        economic_edge_by_direction=_edge_contract(),
    )

    assert proposal is not None
    assert proposal.risk_usd == pytest.approx(
        engine.cfg["base_risk_pct"] * 10_000.0,
        abs=0.01,
    )
    assert proposal.ev == pytest.approx(20.0 / 10_000.0)
    assert "sizing_source=VALIDATED_NET_EDGE_FIXED_RISK" in proposal.rationale
    assert "edge_net_bps=20.000" in proposal.rationale
    assert "ci95_low_net_bps=5.000" in proposal.rationale
    assert "wr_diag=0.600" in proposal.rationale
    assert "raw_ev_diag=0.05000000" in proposal.rationale
    assert "edge_method=PURGED_BLOCK_BOOTSTRAP_V1" in proposal.rationale
    assert f"edge_artifact_sha256={'a' * 64}" in proposal.rationale
    assert f"cost_model_sha256={'b' * 64}" in proposal.rationale
    assert "kelly=" not in proposal.rationale


def test_net_edge_with_nonpositive_lower_bound_fails_closed():
    engine = PortfolioEngine()

    proposal = engine.build_proposal(
        prediction=_prediction(),
        state=_state(),
        vol_pct=0.01,
        win_rate_by_direction={"LONG": 0.90},
        economic_edge_by_direction=_edge_contract(ci95_low_net_bps=0.0),
    )

    assert proposal is None


def test_net_edge_contract_requires_bound_cost_model_hash():
    engine = PortfolioEngine()
    edge = _edge_contract()
    edge["LONG"]["cost_model_sha256"] = "not-a-sha"

    proposal = engine.build_proposal(
        prediction=_prediction(),
        state=_state(),
        vol_pct=0.01,
        win_rate_by_direction={"LONG": 0.90},
        economic_edge_by_direction=edge,
    )

    assert proposal is None

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]