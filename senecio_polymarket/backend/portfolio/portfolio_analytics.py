"""
SENECIO ORACLE — ACT XXV: PortfolioAnalytics (priority 5)
=========================================================

Computes institutional performance metrics from the TradeJournal ledger.
All metrics are computed from closed-trade PnL + equity curve, never from
raw oracle predictions — this is the *execution* layer's report card.

Metrics (per ACT-XXV spec):
  - Sharpe          : annualized return / annualized volatility (risk-free=0)
  - Sortino         : annualized return / annualized downside deviation
  - ProfitFactor    : Σ(wins) / |Σ(losses)|
  - Expectancy      : (win_rate * avg_win) - (loss_rate * avg_loss)  [in $]
  - RecoveryFactor  : total_pnl / max_drawdown_usd
  - Calmar          : annualized return / max_drawdown_pct
  - KellyFraction   : win_rate - (1 - win_rate) / (avg_win / |avg_loss|)
  - MaxDrawdown     : peak-to-trough drawdown on equity curve (pct + usd)

Equity curve construction:
  Starting from `starting_equity`, walk the closed trades in chronological
  order and accumulate realized_pnl_usd. This gives an equity curve
  sampled at every exit. For Sharpe/Sortino, we compute per-trade returns
  (pnl / equity_before_trade), then annualize assuming 15min/trade
  cadence (configurable via `trades_per_year`).

Edge cases:
  - <2 trades: most metrics return 0 (insufficient data)
  - All wins (no losses): ProfitFactor = inf, Sortino = inf, Kelly = 1
  - All losses: ProfitFactor = 0, Kelly = 0
  - Max drawdown = 0: Calmar = inf, RecoveryFactor = inf
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.portfolio_analytics")


DEFAULTS: dict[str, Any] = {
    "starting_equity_usd":  10_000.0,
    "trades_per_year":      35_040,    # 4 per hour × 24 × 365 (15min cadence)
    "risk_free_rate":       0.0,       # 0% risk-free for crypto
    "min_trades_for_metrics": 5,       # need ≥5 trades to compute meaningful stats
}


class PortfolioAnalytics:
    """Compute performance metrics from TradeJournal records.

    Usage:
        analytics = PortfolioAnalytics(config=DEFAULTS)
        trades = journal.fetch_all()
        report = analytics.compute(trades)
        # report = {sharpe, sortino, profit_factor, expectancy, ...}
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        log.info(
            "PortfolioAnalytics init: starting_equity=$%.0f trades_per_year=%d",
            self.cfg["starting_equity_usd"], self.cfg["trades_per_year"],
        )

    def compute(self, trades: list[dict]) -> dict[str, Any]:
        """Compute the full metric suite from a list of trade records.

        Args:
            trades: list of trade dicts (as written by TradeJournal)
        Returns:
            dict with all 8 metrics + supporting stats
        """
        n = len(trades)
        if n == 0:
            return self._empty_report("no_trades")

        # Sort by exit_ts to ensure chronological order
        try:
            trades = sorted(trades, key=lambda t: t.get("exit_ts") or "")
        except Exception:
            pass

        pnls = [float(t.get("realized_pnl_usd") or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        fees = [float(t.get("total_fees_usd") or 0) for t in trades]

        # Equity curve (mark-to-realized)
        equity = self.cfg["starting_equity_usd"]
        equity_curve: list[float] = [equity]
        for p in pnls:
            equity += p
            equity_curve.append(equity)

        # Per-trade returns (pnl / equity_before_trade)
        rets: list[float] = []
        for i, p in enumerate(pnls):
            eq_before = equity_curve[i] if equity_curve[i] != 0 else 1.0
            rets.append(p / eq_before)

        # Compute metrics
        sharpe = self._sharpe(rets)
        sortino = self._sortino(rets)
        profit_factor = self._profit_factor(wins, losses)
        expectancy = self._expectancy(wins, losses, n)
        max_dd_pct, max_dd_usd = self._max_drawdown(equity_curve)
        recovery = self._recovery_factor(pnls, max_dd_usd)
        calmar = self._calmar(rets, max_dd_pct)
        kelly = self._kelly(wins, losses)

        total_pnl = sum(pnls)
        total_fees = sum(fees)
        win_rate = len(wins) / n if n > 0 else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        avg_holding = (
            sum(float(t.get("holding_time_s") or 0) for t in trades) / n
        ) if n > 0 else 0.0
        avg_mae = (
            sum(float(t.get("mae_bps") or 0) for t in trades) / n
        ) if n > 0 else 0.0
        avg_mfe = (
            sum(float(t.get("mfe_bps") or 0) for t in trades) / n
        ) if n > 0 else 0.0

        # Direction breakdown
        by_direction = {}
        for direction in ("LONG", "SHORT"):
            sub = [t for t in trades if (t.get("direction") or "").upper() == direction]
            sub_pnls = [float(t.get("realized_pnl_usd") or 0) for t in sub]
            sub_wins = [p for p in sub_pnls if p > 0]
            sub_losses = [p for p in sub_pnls if p < 0]
            by_direction[direction] = {
                "n": len(sub),
                "win_rate_pct": round(len(sub_wins) / len(sub) * 100, 2) if sub else 0.0,
                "total_pnl_usd": round(sum(sub_pnls), 2),
                "avg_pnl_usd": round(sum(sub_pnls) / len(sub), 2) if sub else 0.0,
                "profit_factor": round(
                    (sum(sub_wins) / abs(sum(sub_losses))) if sub_losses and sum(sub_losses) != 0 else
                    (float("inf") if sub_wins else 0.0), 2,
                ),
            }

        # Exit-reason breakdown
        by_exit_reason = {}
        for t in trades:
            reason = t.get("exit_reason") or "UNKNOWN"
            by_exit_reason.setdefault(reason, {"n": 0, "pnl_usd": 0.0})
            by_exit_reason[reason]["n"] += 1
            by_exit_reason[reason]["pnl_usd"] += float(t.get("realized_pnl_usd") or 0)
        for r in by_exit_reason.values():
            r["pnl_usd"] = round(r["pnl_usd"], 2)

        sufficient = n >= self.cfg["min_trades_for_metrics"]

        return {
            "n_trades": n,
            "sufficient_data": sufficient,
            "starting_equity_usd": self.cfg["starting_equity_usd"],
            "ending_equity_usd": round(eity := equity_curve[-1], 2),
            "total_pnl_usd": round(total_pnl, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_pnl_usd": round(total_pnl - total_fees, 2),
            "total_return_pct": round(
                (total_pnl / self.cfg["starting_equity_usd"]) * 100, 2
            ),
            # Win/loss stats
            "win_rate_pct": round(win_rate * 100, 2),
            "n_wins": len(wins),
            "n_losses": len(losses),
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "win_loss_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss != 0 else float("inf"),
            # Institutional metrics
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "profit_factor": round(profit_factor, 3),
            "expectancy_usd": round(expectancy, 2),
            "recovery_factor": round(recovery, 3) if recovery != float("inf") else float("inf"),
            "calmar": round(calmar, 3) if calmar != float("inf") else float("inf"),
            "kelly_fraction": round(kelly, 4),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            # Quality stats
            "avg_holding_time_s": round(avg_holding, 1),
            "avg_mae_bps": round(avg_mae, 2),
            "avg_mfe_bps": round(avg_mfe, 2),
            "equity_curve": [round(e, 2) for e in equity_curve[-100:]],  # last 100 points
            "by_direction": by_direction,
            "by_exit_reason": by_exit_reason,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    # -------- metric implementations --------

    def _sharpe(self, rets: list[float]) -> float:
        """Annualized Sharpe ratio (risk-free=0)."""
        if len(rets) < 2:
            return 0.0
        mean_r = sum(rets) / len(rets)
        var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        per_trade_sharpe = mean_r / std
        # Annualize: multiply by sqrt(trades_per_year)
        return per_trade_sharpe * math.sqrt(self.cfg["trades_per_year"])

    def _sortino(self, rets: list[float]) -> float:
        """Annualized Sortino ratio (only downside deviation in denominator)."""
        if len(rets) < 2:
            return 0.0
        mean_r = sum(rets) / len(rets)
        downside = [r for r in rets if r < 0]
        if not downside:
            return float("inf") if mean_r > 0 else 0.0
        downside_var = sum(r ** 2 for r in downside) / len(rets)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return float("inf") if mean_r > 0 else 0.0
        per_trade_sortino = mean_r / downside_std
        return per_trade_sortino * math.sqrt(self.cfg["trades_per_year"])

    @staticmethod
    def _profit_factor(wins: list[float], losses: list[float]) -> float:
        """Σ(wins) / |Σ(losses)|."""
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    @staticmethod
    def _expectancy(wins: list[float], losses: list[float], n: int) -> float:
        """Expected $ per trade = (win_rate * avg_win) - (loss_rate * avg_loss)."""
        if n == 0:
            return 0.0
        win_rate = len(wins) / n
        loss_rate = len(losses) / n
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0
        return (win_rate * avg_win) - (loss_rate * avg_loss)

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
        """Peak-to-trough drawdown. Returns (pct, usd)."""
        if len(equity_curve) < 2:
            return 0.0, 0.0
        peak = equity_curve[0]
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for eq in equity_curve[1:]:
            if eq > peak:
                peak = eq
            dd_usd = peak - eq
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
                max_dd_pct = (dd_usd / peak * 100) if peak > 0 else 0.0
        return max_dd_pct, max_dd_usd

    def _recovery_factor(self, pnls: list[float], max_dd_usd: float) -> float:
        """Total PnL / Max Drawdown."""
        if max_dd_usd == 0:
            return float("inf") if sum(pnls) > 0 else 0.0
        return sum(pnls) / max_dd_usd

    def _calmar(self, rets: list[float], max_dd_pct: float) -> float:
        """Annualized return / Max Drawdown %."""
        if max_dd_pct == 0:
            return float("inf") if sum(rets) > 0 else 0.0
        if len(rets) == 0:
            return 0.0
        mean_r = sum(rets) / len(rets)
        annualized_return = mean_r * self.cfg["trades_per_year"] * 100  # in %
        return annualized_return / max_dd_pct

    @staticmethod
    def _kelly(wins: list[float], losses: list[float]) -> float:
        """Kelly fraction: W - (1-W)/R, where W=win_rate, R=avg_win/|avg_loss|."""
        n = len(wins) + len(losses)
        if n == 0:
            return 0.0
        win_rate = len(wins) / n
        if not losses:
            return 1.0 if win_rate > 0 else 0.0
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses))
        if avg_loss == 0:
            return 1.0 if win_rate > 0 else 0.0
        R = avg_win / avg_loss
        if R == 0:
            return 0.0
        kelly = win_rate - (1 - win_rate) / R
        return max(0.0, min(1.0, kelly))   # clamp to [0, 1]

    # -------- helpers --------

    @staticmethod
    def _empty_report(reason: str) -> dict[str, Any]:
        return {
            "n_trades": 0,
            "sufficient_data": False,
            "reason": reason,
            "starting_equity_usd": 0.0,
            "ending_equity_usd": 0.0,
            "total_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "net_pnl_usd": 0.0,
            "total_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "n_wins": 0,
            "n_losses": 0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "win_loss_ratio": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "profit_factor": 0.0,
            "expectancy_usd": 0.0,
            "recovery_factor": 0.0,
            "calmar": 0.0,
            "kelly_fraction": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_usd": 0.0,
            "avg_holding_time_s": 0.0,
            "avg_mae_bps": 0.0,
            "avg_mfe_bps": 0.0,
            "equity_curve": [],
            "by_direction": {},
            "by_exit_reason": {},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    def update_config(self, **overrides: Any) -> None:
        self.cfg.update(overrides)
        log.info("PortfolioAnalytics config updated: %s", overrides)
