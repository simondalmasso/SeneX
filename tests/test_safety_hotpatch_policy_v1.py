import pytest

from senecio_polymarket.backend.portfolio.portfolio_engine import PortfolioEngine
from senecio_polymarket.backend.portfolio.risk_kernel import RiskKernel


@pytest.mark.parametrize("factory", [PortfolioEngine, RiskKernel])
@pytest.mark.parametrize(
    "override",
    [
        {"trade_mode": "LIVE"},
        {"allow_live": True},
        {"live_capital_locked": False},
    ],
)
def test_unsafe_capital_mode_hotpatch_is_rejected(factory, override):
    engine = factory()
    with pytest.raises(RuntimeError, match="HARD_PAPER_LOCK"):
        engine.update_config(**override)


@pytest.mark.parametrize("factory", [PortfolioEngine, RiskKernel])
def test_safe_capital_mode_values_and_normal_config_still_allowed(factory):
    engine = factory()
    engine.update_config(
        trade_mode="PAPER",
        allow_live=False,
        live_capital_locked=True,
        min_confidence=0.55,
    )
    assert engine.cfg["trade_mode"] == "PAPER"
    assert engine.cfg["allow_live"] is False
    assert engine.cfg["live_capital_locked"] is True
    assert engine.cfg["min_confidence"] == pytest.approx(0.55)
