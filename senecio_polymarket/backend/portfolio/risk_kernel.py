"""
SENECIO ORACLE — ACT XXV: RiskKernel (priority 2)
=================================================

Hard risk-gate that approves or rejects trade proposals emitted by the
PortfolioEngine. Runs BEFORE the ExecutionEngine — no order is ever
submitted without first passing through this kernel.

Responsibilities (per ACT-XXV spec):
  - daily loss limit       : halt trading when day-PnL ≤ -max_daily_loss_pct
  - kill switch            : manual + automatic hard halt; once tripped, all
                             proposals REJECTED until explicitly reset
  - max drawdown           : halt trading when drawdown from peak equity
                             exceeds max_drawdown_pct
  - volatility scaling     : shrink size when realized vol exceeds vol_threshold
  - confidence filter      : reject proposals whose confidence < min_confidence
  - cooldown after losses  : after N consecutive losses, pause M minutes

State:
  The kernel keeps an in-memory state that includes:
    - daily_pnl (reset each UTC midnight)
    - peak_equity (high-water mark for drawdown)
    - consecutive_losses
    - last_loss_ts
    - kill_switch_active + reason
    - vol_regime (LOW / NORMAL / HIGH / EXTREME)

Decision API:
  decision = kernel.evaluate(proposal, market_context)
  decision.approved   -> bool
  decision.reason     -> str
  decision.size_scale -> float (1.0 default; <1.0 when vol-scaled)
  decision.kernel_state -> dict snapshot

The ExecutionEngine only fills proposals where decision.approved is True
and applies size_scale to the proposal's size_qty / size_usd.

NO LIVE TRADING: per ACT-XXV LIVE_GATE, even if this kernel approves,
trade_mode stays PAPER and live_capital_locked stays True. The kernel
does NOT enforce the LIVE_GATE itself — that's enforced by the
LIVE_GATE evaluator at the wiring layer (main.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

log = logging.getLogger("senecio.risk_kernel")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # Daily loss / drawdown
    "max_daily_loss_pct":       0.030,    # halt when day-PnL ≤ -3% of equity
    "max_drawdown_pct":         0.10,     # halt when DD from peak > 10%
    "starting_equity_usd":      10_000.0,
    # Confidence filter
    "min_confidence":           0.40,     # rejects below this (sigmoid midpoint territory)
    # Volatility scaling
    "vol_threshold_normal":     0.015,    # <1.5% realized vol → LOW/NORMAL
    "vol_threshold_high":       0.030,    # 1.5-3% → NORMAL, 3-5% → HIGH (scale 0.5x)
    "vol_threshold_extreme":    0.050,    # >5% → EXTREME (scale 0.25x or reject)
    "vol_scale_high":           0.50,
    "vol_scale_extreme":        0.25,
    # Cooldown after losses
    "consecutive_loss_threshold": 3,      # 3 losses in a row → cooldown
    "cooldown_minutes":           30,     # 30-minute cooldown
    # Kill switch
    "kill_switch_active":         False,  # manual override
    "kill_switch_reason":         "",
    # LIVE_GATE passthrough (informational — actual lock enforced by main.py)
    "trade_mode":                 "PAPER",
    "live_capital_locked":        True,
}


class VolRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


# -------------------- data classes --------------------

@dataclass
class RiskDecision:
    """Output of RiskKernel.evaluate()."""
    approved: bool
    reason: str
    size_scale: float = 1.0
    kernel_state: dict = field(default_factory=dict)
    proposal_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KernelState:
    """Mutable state held by the kernel between evaluate() calls."""
    # Daily PnL tracking
    current_day: str                       # YYYY-MM-DD (UTC)
    daily_pnl_usd: float = 0.0
    daily_pnl_pct: float = 0.0
    # Drawdown tracking
    peak_equity: float = 0.0
    current_equity: float = 0.0
    drawdown_pct: float = 0.0
    # Loss streak + cooldown
    consecutive_losses: int = 0
    last_loss_ts: Optional[str] = None
    cooldown_until: Optional[str] = None
    # Kill switch
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    kill_switch_set_at: Optional[str] = None
    # Volatility regime (updated externally)
    vol_regime: str = "NORMAL"
    vol_pct: float = 0.0
    # Counters
    proposals_evaluated: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- RiskKernel --------------------

class RiskKernel:
    """Hard risk-gate between PortfolioEngine and ExecutionEngine.

    Usage:
        kernel = RiskKernel(config=DEFAULTS)
        kernel.init_state(starting_equity=10_000.0)

        # on every fill exit:
        kernel.record_pnl(pnl_usd=23.50, equity=10_023.50)
        # on every market tick:
        kernel.update_vol_regime(vol_pct=0.018)

        # on every proposal:
        decision = kernel.evaluate(proposal)
        if decision.approved:
            scaled_qty = proposal.size_qty * decision.size_scale
            # hand to ExecutionEngine
    """

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.state = KernelState(
            current_day=self._today_utc(),
            peak_equity=self.cfg["starting_equity_usd"],
            current_equity=self.cfg["starting_equity_usd"],
        )
        # ACT-XXVI: optional MicrostructureIntelligence observer.
        # If set, evaluate() consults it for an additional toxic-flow check
        # that can REJECT or REDUCE size based on VPIN/OFI/liquidation/funding.
        # Stays None by default so existing tests / behavior are unchanged.
        self.microstructure = None
        log.info(
            "RiskKernel init: starting_equity=$%.0f max_daily_loss=%.2f%% max_dd=%.2f%% "
            "min_conf=%.2f cooldown_after=%d losses for %dm",
            self.cfg["starting_equity_usd"],
            self.cfg["max_daily_loss_pct"] * 100,
            self.cfg["max_drawdown_pct"] * 100,
            self.cfg["min_confidence"],
            self.cfg["consecutive_loss_threshold"],
            self.cfg["cooldown_minutes"],
        )

    # -------- public API --------

    def init_state(self, starting_equity: float) -> None:
        """(Re)initialize state with a fresh equity baseline."""
        self.state = KernelState(
            current_day=self._today_utc(),
            peak_equity=starting_equity,
            current_equity=starting_equity,
        )
        self.cfg["starting_equity_usd"] = starting_equity
        log.info("RiskKernel state reset: equity=$%.2f", starting_equity)

    def evaluate(
        self,
        proposal: Any,                 # TradeProposal (duck-typed via .to_dict())
        market_context: Optional[dict[str, Any]] = None,
    ) -> RiskDecision:
        """Run all risk checks against a proposal. Returns a RiskDecision."""
        self.state.proposals_evaluated += 1
        snap = self.state.to_dict()

        # 0) Kill switch — manual or auto
        if self.state.kill_switch_active:
            return self._reject(
                proposal, f"kill_switch_active: {self.state.kill_switch_reason}", snap
            )

        # 1) LIVE_GATE lock — informational, never approve LIVE while locked
        if self.cfg.get("trade_mode") != "PAPER" or self.cfg.get("live_capital_locked"):
            # Paper mode continues — kernel still evaluates for audit/learning
            pass

        # 2) Daily loss limit
        if self.state.daily_pnl_pct <= -self.cfg["max_daily_loss_pct"] * 100:
            return self._reject(
                proposal,
                f"daily_loss_limit_hit: pnl_pct={self.state.daily_pnl_pct:.2f}% "
                f"threshold=-{self.cfg['max_daily_loss_pct']*100:.2f}%",
                snap,
            )

        # 3) Max drawdown
        if self.state.drawdown_pct >= self.cfg["max_drawdown_pct"] * 100:
            return self._reject(
                proposal,
                f"max_drawdown_hit: dd={self.state.drawdown_pct:.2f}% "
                f"threshold={self.cfg['max_drawdown_pct']*100:.2f}%",
                snap,
            )

        # 4) Confidence filter
        p = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal)
        conf = float(p.get("confidence", 0))
        if conf < self.cfg["min_confidence"]:
            return self._reject(
                proposal,
                f"low_confidence: {conf:.3f} < {self.cfg['min_confidence']:.3f}",
                snap,
            )

        # 5) Cooldown after losses
        if self.state.cooldown_until:
            try:
                cd_until = datetime.fromisoformat(self.state.cooldown_until.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < cd_until:
                    remaining = (cd_until - datetime.now(timezone.utc)).total_seconds() / 60
                    return self._reject(
                        proposal,
                        f"cooldown_active: {remaining:.1f}m remaining "
                        f"(after {self.state.consecutive_losses} consecutive losses)",
                        snap,
                    )
                else:
                    # cooldown expired — clear it
                    self.state.cooldown_until = None
                    log.info("cooldown cleared — resuming trading")
            except Exception:
                pass

        # 6) Volatility scaling — doesn't reject, but shrinks size
        size_scale = 1.0
        regime = VolRegime(self.state.vol_regime)
        if regime == VolRegime.HIGH:
            size_scale = self.cfg["vol_scale_high"]
        elif regime == VolRegime.EXTREME:
            size_scale = self.cfg["vol_scale_extreme"]
            # Optionally reject in EXTREME — for now we scale to 25%
            log.warning(
                "EXTREME vol regime (vol_pct=%.2f%%) — scaling size to %.0f%%",
                self.state.vol_pct * 100, size_scale * 100,
            )

        # 7) ACT-XXVI: Microstructure toxic-flow check (additive, optional)
        # If a MicrostructureIntelligence observer is attached and the
        # current market state exceeds the toxic thresholds, REJECT or
        # further REDUCE the size. This blocks new entries during broken
        # zones (high VPIN, one-sided OFI, near liquidation clusters,
        # extreme funding/OI divergence).
        micro_report = None
        if self.microstructure is not None:
            try:
                # Use the proposal's entry price + direction for evaluation
                entry_price = float(p.get("entry_price", 0))
                direction = p.get("direction", "LONG")
                micro_report = self.microstructure.evaluate(
                    current_price=entry_price,
                    direction=direction,
                )
                if micro_report.action == "REJECT":
                    return self._reject(
                        proposal,
                        f"microstructure_reject: toxic_score={micro_report.toxic_score:.2f} "
                        f"vpin={micro_report.vpin:.2f} ofi={micro_report.ofi_normalized:.2f} "
                        f"near_liq={micro_report.near_liquidation_cluster} "
                        f"funding_extreme={micro_report.funding_extreme}",
                        snap,
                    )
                if micro_report.action == "REDUCE":
                    # Multiply the vol-scale by the microstructure reduce factor
                    size_scale *= micro_report.size_scale
                    log.info(
                        "microstructure REDUCE: toxic=%.2f → size_scale=%.2f",
                        micro_report.toxic_score, size_scale,
                    )
            except Exception as e:
                log.warning("microstructure evaluate failed (non-fatal): %s", e)

        # 8) Day rollover check (best-effort)
        self._maybe_rollover_day()

        # All checks passed
        self.state.proposals_approved += 1
        reason = (
            f"approved conf={conf:.3f} regime={regime.value} "
            f"size_scale={size_scale:.2f} dd={self.state.drawdown_pct:.2f}% "
            f"day_pnl={self.state.daily_pnl_pct:.2f}%"
            + (f" micro_toxic={micro_report.toxic_score:.2f}" if micro_report else "")
        )
        return RiskDecision(
            approved=True,
            reason=reason,
            size_scale=size_scale,
            kernel_state=self.state.to_dict(),
            proposal_id=p.get("prediction_id"),
        )

    def record_pnl(self, pnl_usd: float, equity: float) -> None:
        """Update internal state after a closed position.

        Called by ExecutionEngine (or its caller) after every exit.
        """
        # Update daily PnL (with day-rollover safety)
        self._maybe_rollover_day()
        self.state.daily_pnl_usd += pnl_usd
        base = max(self.cfg["starting_equity_usd"], 1.0)
        self.state.daily_pnl_pct = (self.state.daily_pnl_usd / base) * 100

        # Update equity + drawdown
        self.state.current_equity = equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity
        if self.state.peak_equity > 0:
            self.state.drawdown_pct = max(
                0.0,
                (self.state.peak_equity - equity) / self.state.peak_equity * 100,
            )

        # Update loss streak
        if pnl_usd < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_ts = datetime.now(timezone.utc).isoformat()
            if self.state.consecutive_losses >= self.cfg["consecutive_loss_threshold"]:
                cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.cfg["cooldown_minutes"]
                )
                self.state.cooldown_until = cooldown_until.isoformat()
                log.warning(
                    "cooldown triggered: %d consecutive losses → cooldown until %s",
                    self.state.consecutive_losses, self.state.cooldown_until,
                )
                # Auto-kill-switch if losses continue during cooldown — escalate
                if self.state.consecutive_losses >= self.cfg["consecutive_loss_threshold"] * 2:
                    self.trip_kill_switch(
                        f"auto: {self.state.consecutive_losses} consecutive losses"
                    )
        else:
            # Win resets the loss streak
            if pnl_usd > 0:
                self.state.consecutive_losses = 0
                self.state.cooldown_until = None

        # Auto-kill on daily loss limit breach
        if self.state.daily_pnl_pct <= -self.cfg["max_daily_loss_pct"] * 100:
            self.trip_kill_switch(
                f"auto: daily_loss={self.state.daily_pnl_pct:.2f}% "
                f"reached -{self.cfg['max_daily_loss_pct']*100:.2f}%"
            )

        # Auto-kill on max drawdown breach
        if self.state.drawdown_pct >= self.cfg["max_drawdown_pct"] * 100:
            self.trip_kill_switch(
                f"auto: drawdown={self.state.drawdown_pct:.2f}% "
                f"reached {self.cfg['max_drawdown_pct']*100:.2f}%"
            )

    def update_vol_regime(self, vol_pct: float) -> None:
        """Update the volatility regime (called externally with realized vol)."""
        self.state.vol_pct = vol_pct
        if vol_pct < self.cfg["vol_threshold_normal"]:
            self.state.vol_regime = VolRegime.LOW.value
        elif vol_pct < self.cfg["vol_threshold_high"]:
            self.state.vol_regime = VolRegime.NORMAL.value
        elif vol_pct < self.cfg["vol_threshold_extreme"]:
            self.state.vol_regime = VolRegime.HIGH.value
        else:
            self.state.vol_regime = VolRegime.EXTREME.value

    def trip_kill_switch(self, reason: str) -> None:
        """Manually or automatically trip the kill switch."""
        self.state.kill_switch_active = True
        self.state.kill_switch_reason = reason
        self.state.kill_switch_set_at = datetime.now(timezone.utc).isoformat()
        log.error("KILL SWITCH TRIPPED: %s", reason)

    def reset_kill_switch(self, reason: str = "manual reset") -> None:
        """Clear the kill switch (requires explicit human action)."""
        log.warning("KILL SWITCH RESET: %s (was: %s)", reason, self.state.kill_switch_reason)
        self.state.kill_switch_active = False
        self.state.kill_switch_reason = ""
        self.state.kill_switch_set_at = None
        # Also reset cooldown + loss streak so we can resume
        self.state.cooldown_until = None
        self.state.consecutive_losses = 0

    def get_state(self) -> dict[str, Any]:
        """Snapshot for /api/risk/state endpoint."""
        return self.state.to_dict()

    def update_config(self, **overrides: Any) -> None:
        """Hot-patch config."""
        self.cfg.update(overrides)
        log.info("RiskKernel config updated: %s", overrides)

    # -------- internal helpers --------

    def _reject(self, proposal: Any, reason: str, snap: dict) -> RiskDecision:
        self.state.proposals_rejected += 1
        p = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal)
        log.info("proposal REJECTED: %s %s — %s", p.get("symbol"), p.get("direction"), reason)
        return RiskDecision(
            approved=False,
            reason=reason,
            size_scale=0.0,
            kernel_state=snap,
            proposal_id=p.get("prediction_id"),
        )

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_rollover_day(self) -> None:
        """Reset daily PnL counters at UTC midnight."""
        today = self._today_utc()
        if today != self.state.current_day:
            log.info(
                "daily rollover: %s → %s (resetting daily_pnl from $%.2f / %.2f%%)",
                self.state.current_day, today,
                self.state.daily_pnl_usd, self.state.daily_pnl_pct,
            )
            self.state.current_day = today
            self.state.daily_pnl_usd = 0.0
            self.state.daily_pnl_pct = 0.0
            # If kill switch was tripped by daily-loss, auto-reset on new day
            if (
                self.state.kill_switch_active
                and "daily_loss" in (self.state.kill_switch_reason or "")
            ):
                self.reset_kill_switch("auto: new trading day")
