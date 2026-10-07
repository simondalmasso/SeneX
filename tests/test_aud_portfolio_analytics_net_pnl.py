[Reading 59 lines from start (total: 59 lines, 0 remaining)]

from senecio_polymarket.backend.portfolio.portfolio_analytics import PortfolioAnalytics


def _trade(
    trade_id: str,
    pnl: float,
    fees: float,
    *,
    direction: str,
    exit_ts: str,
):
    return {
        "trade_id": trade_id,
        "direction": direction,
        "exit_ts": exit_ts,
        "realized_pnl_usd": pnl,
        "total_fees_usd": fees,
        "holding_time_s": 3600,
        "mae_bps": -10.0,
        "mfe_bps": 15.0,
        "exit_reason": "TIME_STOP",
    }


def test_portfolio_analytics_does_not_double_subtract_fees_from_realized_pnl():
    analytics = PortfolioAnalytics(
        config={
            "starting_equity_usd": 1000.0,
            "min_trades_for_metrics": 1,
            "trades_per_year": 365.0,
        }
    )
    trades = [
        _trade(
            "t1",
            8.0,   # already net of the $2 entry+exit fees
            2.0,
            direction="LONG",
            exit_ts="2026-10-01T01:00:00Z",
        ),
        _trade(
            "t2",
            -6.0,  # already net of the $1 entry+exit fees
            1.0,
            direction="SHORT",
            exit_ts="2026-10-01T02:00:00Z",
        ),
    ]

    report = analytics.compute(trades)

    assert report["total_pnl_usd"] == 2.0
    assert report["total_fees_usd"] == 3.0
    assert report["net_pnl_usd"] == 2.0
    assert report["ending_equity_usd"] == 1002.0
    assert report["net_pnl_usd"] == report["ending_equity_usd"] - report["starting_equity_usd"]
    assert report["total_return_pct"] == 0.2
    assert report["expectancy_usd"] == 1.0
    assert report["expectancy_usd"] == report["net_pnl_usd"] / report["n_trades"]

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]