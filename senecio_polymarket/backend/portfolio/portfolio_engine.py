"""
SENECIO ORACLE — ACT XXV: PortfolioEngine (priority 1)
======================================================

Converts oracle predictions into sized, exposure-controlled trade proposals.

Responsibilities (per ACT-XXV spec):
  - position sizing            : risk-fractional sizing keyed off confidence + volatility
  - exposure control           : gross + net exposure caps, per-symbol cap
  - multi-position support     : up to N concurrent positions across symbols
  - portfolio heat             : sum of |risk_per_position| / equity capped at heat_max
  - max concurrent trades      : hard ceiling on open positions

This module is ADDITIVE — it does NOT touch the prediction model, feature
engineering, signal generation, or verifier (all in DO_NOT_TOUCH). It
receives an oracle prediction dict and produces a TradeProposal that the
RiskKernel will approve/reject and the ExecutionEngine will fill.

Inputs (prediction dict from oracle_runner):
  {
    "symbol": "ETH/USDT",
    "prediction": "LONG" | "SHORT" | "FLAT",
    "confidence": 0.0..1.0,
    "ev": float,
    "price_now": float,
    "_audit": {...},
    ...
  }

Outputs (TradeProposal):
  {
    "symbol": str,
    "direction": "LONG" | "SHORT",
    "size_usd": float,            # notional USD to deploy
    "size_qty": float,            # qty = size_usd / entry_price
    "entry_price": float,         # reference price (will be re-priced by exec engine)
    "stop_price": float,          # stop-loss reference
    "target_price": float,        # take-profit reference
    "risk_per_unit": float,       # |entry - stop|
    "risk_usd": float,            # size_qty * risk_per_unit (the $ at risk if stop hit)
    "confidence": float,          # passthrough
    "ev": float,                  # passthrough
    "source": "oracle",           # provenance tag
    "prediction_id": str | int,   # FK to oracle_predictions row
    "rationale": str,             # why this size (e.g. "kelly_q=0.12 cap=2.0%")
  }

Sizing model:
  base_risk_pct  = 0.5%  of equity  (per-trade risk budget)
  confidence_mult = sigmoid((conf - 0.50) * 8)  → 0.12..0.99
  kelly_fraction  = clamp(2*win_rate - 1 + edge_adjust, 0, 0.25)
  sizing_pct      = min(base_risk_pct * confidence_mult, max_pct=2.0%)
  stop_distance   = max(volatility_stop, fixed_2pct)
  size_qty        = sizing_pct * equity / stop_distance

Exposure caps:
  max_concurrent     = 3
  max_per_symbol     = 1
  max_gross_exposure = 1.5 * equity   (150%)
  max_net_exposure   = 1.0 * equity   (100% long or short)
  heat_max           = 4.5%           (sum of risk_usd / equity across all open positions)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.portfolio_engine")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # Equity + base risk
    "starting_equity_usd": 10_000.0,
    "base_risk_pct":           0.005,   # 0.5% per trade
    "max_risk_pct_per_trade":  0.020,   # hard cap at 2.0% per trade
    "kelly_cap":               0.25,    # never deploy more than 25% of Kelly
    "kelly_floor":             0.0,     # negative Kelly → no trade
    # Exposure caps
    "max_concurrent":          3,
    "max_per_symbol":          1,
    "max_gross_exposure_pct":  1.50,    # 150% of equity
    "max_net_exposure_pct":    1.00,    # 100% long or 100% short
    "heat_max_pct":            0.045,   # 4.5% portfolio heat cap
    # Stops / targets
    "fixed_stop_pct":          0.020,   # 2% stop
    "fixed_target_pct":        0.040,   # 4% target (2:1 R/R)
    "vol_stop_lookback":       16,      # 16 candles (4h on 15m timeframe)
    "vol_stop_multiplier":     1.50,    # 1.5× ATR-equivalent
    "min_stop_pct":            0.005,   # 0.5% floor — avoid ultra-tight stops
    "max_stop_pct":            0.060,   # 6% ceiling — avoid ultra-wide stops
    # Confidence shaping
    "conf_k":                  8.0,     # sigmoid steepness
    "conf_mid":                0.50,    # sigmoid midpoint
    # Directional gating passthrough — set externally by oracle_runner
    "short_only_paper_mode":   False,
    "trade_mode":              "PAPER",
    "live_capital_locked":     True,
}


# -------------------- data classes --------------------

@dataclass
class TradeProposal:
    """A sized, exposure-checked trade proposal ready for RiskKernel review."""
    symbol: str
    direction: str                     # "LONG" | "SHORT"
    size_usd: float
    size_qty: float
    entry_price: float
    stop_price: float
    target_price: float
    risk_per_unit: float
    risk_usd: float
    confidence: float
    ev: float
    source: str = "oracle"
    prediction_id: Optional[str | int] = None
    rationale: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = d.get("created_at") or datetime.now(timezone.utc).isoformat()
        return d


@dataclass
class PortfolioState:
    """Live portfolio snapshot — updated after every fill/exit."""
    equity: float                                 # current equity (cash + unrealized MTM)
    cash: float                                   # free cash
    open_positions: dict[str, dict] = field(default_factory=dict)  # symbol → position dict
    realized_pnl: float = 0.0
    gross_exposure_usd: float = 0.0
    net_exposure_usd: float = 0.0
    portfolio_heat_pct: float = 0.0
    open_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- PortfolioEngine --------------------

class PortfolioEngine:
    """Converts oracle predictions into sized trade proposals.

    Stateless w.r.t. the prediction model — receives a prediction dict,
    consults the live PortfolioState (which is updated externally by the
    ExecutionEngine after fills), and emits a TradeProposal or None.

    Usage:
        engine = PortfolioEngine(config=DEFAULTS)
        proposal = engine.build_proposal(prediction_dict, state=state, vol_pct=0.012)
        if proposal:
            # hand to RiskKernel for approval
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        # ACT-XXVI: optional MetaLabeler for LONG-side secondary filtering.
        # If set, build_proposal() consults it AFTER computing stop/target
        # but BEFORE final Kelly sizing. If the label says take_trade=False,
        # the proposal is dropped. If True, the proposal's confidence is
        # multiplied by the labeler's confidence_mult.
        # Stays None by default so existing tests / behavior are unchanged.
        self.meta_labeler = None
        log.info(
            "PortfolioEngine init: equity=$%.0f base_risk=%.2f%% max_concurrent=%d heat_max=%.2f%%",
            self.cfg["starting_equity_usd"],
            self.cfg["base_risk_pct"] * 100,
            self.cfg["max_concurrent"],
            self.cfg["heat_max_pct"] * 100,
        )

    # -------- public API --------

    def build_proposal(
        self,
        prediction: dict[str, Any],
        state: PortfolioState,
        vol_pct: Optional[float] = None,
        win_rate_by_direction: Optional[dict[str, float]] = None,
    ) -> Optional[TradeProposal]:
        """Convert a prediction dict into a sized TradeProposal.

        Returns None when:
          - prediction is FLAT or missing
          - SHORT_ONLY_PAPER_MODE is on and prediction is LONG
          - max_concurrent reached
          - symbol already has an open position (max_per_symbol)
          - gross/net/heat exposure would be breached
          - sizing would round to zero qty
          - Kelly says no-edge (kelly_fraction <= kelly_floor)

        Args:
            prediction: oracle prediction dict
            state: live PortfolioState (read-only)
            vol_pct: realized volatility as fraction (e.g. 0.012 for 1.2%) —
                     used to size the stop. If None, uses fixed_stop_pct.
            win_rate_by_direction: e.g. {"LONG": 0.49, "SHORT": 0.56}. Used
                     to compute Kelly fraction. If None, uses a flat 0.50.
        """
        cfg = self.cfg
        direction = (prediction.get("prediction") or "").upper()
        symbol = prediction.get("symbol") or ""
        price_now = float(prediction.get("price_now") or 0)
        confidence = float(prediction.get("confidence") or 0)
        ev = float(prediction.get("ev") or 0)
        pred_id = prediction.get("id") or prediction.get("timestamp")

        # 1) Direction gating
        if direction not in ("LONG", "SHORT"):
            return None
        if direction == "LONG" and cfg.get("short_only_paper_mode"):
            self._log_skip(symbol, direction, "short_only_paper_mode blocks LONG")
            return None
        if cfg.get("trade_mode") != "PAPER" or cfg.get("live_capital_locked"):
            # Per ACT-XXV LIVE_GATE: trade_mode stays PAPER until all 6 unlock
            # conditions are met. Proposals are still built (for shadow mode),
            # but downstream ExecutionEngine will not place real orders.
            pass

        # 2) Price sanity
        if price_now <= 0:
            self._log_skip(symbol, direction, f"invalid price_now={price_now}")
            return None

        # 3) Concurrency + per-symbol caps
        if state.open_count >= cfg["max_concurrent"]:
            self._log_skip(symbol, direction, f"max_concurrent={cfg['max_concurrent']} reached")
            return None
        if symbol in state.open_positions:
            self._log_skip(symbol, direction, "symbol already has open position")
            return None

        # 4) Stop distance — max(vol_stop, fixed_stop), clamped to [min, max]
        vol_stop = (vol_pct or 0) * cfg["vol_stop_multiplier"]
        fixed_stop = cfg["fixed_stop_pct"]
        stop_pct = max(vol_stop, fixed_stop)
        stop_pct = max(cfg["min_stop_pct"], min(cfg["max_stop_pct"], stop_pct))

        if direction == "LONG":
            stop_price = price_now * (1 - stop_pct)
            target_price = price_now * (1 + cfg["fixed_target_pct"])
            risk_per_unit = price_now - stop_price
        else:  # SHORT
            stop_price = price_now * (1 + stop_pct)
            target_price = price_now * (1 - cfg["fixed_target_pct"])
            risk_per_unit = stop_price - price_now

        if risk_per_unit <= 0:
            self._log_skip(symbol, direction, "risk_per_unit<=0")
            return None

        # 4.5) ACT-XXVI: Meta-labeling (LONG-only secondary filter)
        # If a MetaLabeler is attached, run it BEFORE Kelly sizing. A REJECT
        # verdict drops the proposal entirely; an ACCEPT verdict multiplies
        # the effective confidence (which then feeds the Kelly sizing below).
        meta_label = None
        if self.meta_labeler is not None:
            try:
                # Extract context for the labeler
                regime_4h = (prediction.get("_audit") or {}).get("regime_4h") or "NEUTRAL"
                spread_bps = (prediction.get("_audit") or {}).get("spread_bps", 0.0) or 0.0
                ev_bps = abs(ev) * 10_000  # ev is a fraction; convert to bps
                meta_label = self.meta_labeler.evaluate(
                    direction=direction,
                    conviction=confidence,
                    regime_4h=str(regime_4h),
                    vol_pct=vol_pct or 0.01,
                    spread_bps=float(spread_bps),
                    entry_price=price_now,
                    stop_price=stop_price,
                    target_price=target_price,
                    expected_ev_bps=float(ev_bps),
                    time_stop_minutes=60,
                )
                if not meta_label.take_trade:
                    self._log_skip(
                        symbol, direction,
                        f"meta_label_reject: {meta_label.reason}",
                    )
                    return None
                # Apply confidence multiplier (this then flows into Kelly sizing)
                confidence = confidence * meta_label.confidence_mult
                log.info(
                    "meta_label PASS: %s %s mult=%.2f → conf=%.3f barrier=%s rr=%.2f",
                    symbol, direction, meta_label.confidence_mult, confidence,
                    meta_label.barrier_hit_prediction, meta_label.reward_risk,
                )
            except Exception as e:
                log.warning("meta_labeler evaluate failed (non-fatal): %s", e)

        # 5) Confidence-shaped risk fraction
        conf_mult = self._sigmoid(
            (confidence - cfg["conf_mid"]) * cfg["conf_k"]
        )  # → 0..1
        risk_pct = cfg["base_risk_pct"] * conf_mult

        # 6) Kelly cap (uses per-direction win rate if available)
        wr = (win_rate_by_direction or {}).get(direction, 0.50)
        kelly = max(0.0, 2 * wr - 1)   # full-Kelly fraction
        kelly = min(kelly, cfg["kelly_cap"])
        if kelly <= cfg["kelly_floor"]:
            self._log_skip(
                symbol, direction,
                f"no kelly edge wr={wr:.3f} kelly={kelly:.3f}",
            )
            return None
        # Use min of confidence-shaped and Kelly-capped risk
        risk_pct = min(risk_pct, kelly * 0.10)   # 10% of Kelly deployed per trade
        risk_pct = min(risk_pct, cfg["max_risk_pct_per_trade"])

        # 7) Size in USD = risk_pct * equity, then convert to qty via stop distance
        risk_usd = risk_pct * state.equity
        size_qty = risk_usd / risk_per_unit
        size_usd = size_qty * price_now

        if size_qty <= 0 or size_usd <= 1.0:
            self._log_skip(symbol, direction, f"size too small qty={size_qty:.6f} usd={size_usd:.2f}")
            return None

        # 8) Exposure checks (gross / net / heat)
        new_gross = state.gross_exposure_usd + size_usd
        new_net = state.net_exposure_usd + (size_usd if direction == "LONG" else -size_usd)
        new_heat = state.portfolio_heat_pct + (risk_usd / state.equity)

        if new_gross > cfg["max_gross_exposure_pct"] * state.equity:
            self._log_skip(symbol, direction, f"gross cap breach new_gross=${new_gross:.0f}")
            return None
        if abs(new_net) > cfg["max_net_exposure_pct"] * state.equity:
            self._log_skip(symbol, direction, f"net cap breach new_net=${new_net:.0f}")
            return None
        if new_heat > cfg["heat_max_pct"]:
            self._log_skip(symbol, direction, f"heat cap breach new_heat={new_heat*100:.2f}%")
            return None

        rationale = (
            f"conf={confidence:.3f} conf_mult={conf_mult:.3f} "
            f"wr={wr:.3f} kelly={kelly:.3f} risk_pct={risk_pct*100:.3f}% "
            f"stop_pct={stop_pct*100:.2f}% vol_stop={vol_stop*100:.2f}%"
            + (f" meta={meta_label.barrier_hit_prediction}/{meta_label.reward_risk:.2f}" if meta_label else "")
        )

        proposal = TradeProposal(
            symbol=symbol,
            direction=direction,
            size_usd=round(size_usd, 2),
            size_qty=round(size_qty, 6),
            entry_price=round(price_now, 6),
            stop_price=round(stop_price, 6),
            target_price=round(target_price, 6),
            risk_per_unit=round(risk_per_unit, 6),
            risk_usd=round(risk_usd, 2),
            confidence=round(confidence, 4),
            ev=round(ev, 8),
            prediction_id=pred_id,
            rationale=rationale,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info(
            "proposal built: %s %s size=$%.2f qty=%.6f stop=$%.4f risk=$%.2f [%s]",
            symbol, direction, size_usd, size_qty, stop_price, risk_usd, rationale,
        )
        return proposal

    def recompute_state(
        self,
        open_positions: dict[str, dict],
        cash: float,
        starting_equity: float,
        last_prices: dict[str, float],
    ) -> PortfolioState:
        """Recompute the full PortfolioState from a position map.

        Called by ExecutionEngine after every fill/exit so the next
        build_proposal() call sees a consistent snapshot.
        """
        gross = 0.0
        net = 0.0
        heat = 0.0
        equity = cash
        open_count = 0
        clean_positions: dict[str, dict] = {}
        for sym, p in open_positions.items():
            if p.get("status") != "OPEN":
                continue
            open_count += 1
            qty = float(p.get("qty", 0))
            entry = float(p.get("entry_price", 0))
            direction = p.get("direction", "LONG").upper()
            last = last_prices.get(sym, entry)
            notional = qty * last
            gross += notional
            net += notional if direction == "LONG" else -notional
            risk_usd = float(p.get("risk_usd", 0))
            heat += risk_usd / max(starting_equity, 1.0)
            # mark-to-market equity contribution
            if direction == "LONG":
                equity += (last - entry) * qty
            else:
                equity += (entry - last) * qty
            clean_positions[sym] = p

        return PortfolioState(
            equity=round(equity, 2),
            cash=round(cash, 2),
            open_positions=clean_positions,
            realized_pnl=0.0,   # tracked separately by TradeJournal
            gross_exposure_usd=round(gross, 2),
            net_exposure_usd=round(net, 2),
            portfolio_heat_pct=round(heat, 4),
            open_count=open_count,
        )

    # -------- helpers --------

    @staticmethod
    def _sigmoid(x: float) -> float:
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        z = math.exp(x)
        return z / (1.0 + z)

    @staticmethod
    def _log_skip(symbol: str, direction: str, reason: str) -> None:
        log.info("proposal skipped: %s %s — %s", symbol, direction, reason)

    def update_config(self, **overrides: Any) -> None:
        """Hot-patch config (e.g. enable short_only_paper_mode)."""
        self.cfg.update(overrides)
        log.info("PortfolioEngine config updated: %s", overrides)
