from unittest.mock import patch

from senecio_polymarket.backend.portfolio.risk_kernel import RiskKernel


def test_new_day_rollover_clears_daily_loss_kill_switch_before_evaluate():
    kernel = RiskKernel(config={"min_confidence": 0.5})
    kernel.state.current_day = "2026-09-11"
    kernel.state.daily_pnl_usd = -600.0
    kernel.state.daily_pnl_pct = -6.0
    kernel.state.kill_switch_active = True
    kernel.state.kill_switch_reason = "daily_loss_limit_hit"
    proposal = {
        "prediction_id": "p-new-day",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_price": 100.0,
        "confidence": 0.9,
    }

    with patch.object(kernel, "_today_utc", return_value="2026-09-12"):
        decision = kernel.evaluate(proposal)

    assert kernel.state.current_day == "2026-09-12"
    assert kernel.state.daily_pnl_usd == 0.0
    assert kernel.state.daily_pnl_pct == 0.0
    assert kernel.state.kill_switch_active is False
    assert decision.approved is True
