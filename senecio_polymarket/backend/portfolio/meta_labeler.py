"""
SENECIO ORACLE — ACT XXVI: Meta-Labeling (priority 2)
======================================================

Secondary classifier (López de Prado / MLFinLab "meta-labeling") that
sits AFTER the primary oracle signal but BEFORE the portfolio engine.

WHY THIS MODULE EXISTS
-----------------------
The oracle's primary signal answers: "what direction?" (LONG / SHORT /
NEUTRAL). It does NOT answer the secondary question: "given this LONG
signal, should I actually take the trade, and with what confidence?"

A real meta-labeler is trained on historical (primary_signal, features)
→ realized_outcome pairs. In PAPER mode we don't yet have enough
verified outcomes to train a real ML model. So we ship an **explicit
heuristic meta-labeler** that approximates the triple-barrier logic
until the labeled sample size crosses 300 (the LIVE_GATE threshold).

WHAT THIS MODULE ADDS (additive — does NOT modify PortfolioEngine directly)
-----------------------------------------------------------------------------
  - `MetaLabel`               : output dataclass (take_trade, confidence_mult, reason)
  - `TripleBarrier`           : the three barriers (upper, lower, vertical)
  - `MetaLabeler`             : the explicit classifier
  - Heuristic rules (per López de Prado's book §3.6 + practical crypto):
      1. **Trend alignment** — LONG signal must align with 4h trend
         (regime in {BULL, NEUTRAL}; not BEAR). The primary oracle already
         has a regime_filter_4h but it's a HARD gate; here we use it as
         a SOFT multiplier on confidence.
      2. **Conviction threshold** — primary conviction must clear a
         LONG-specific floor (default 0.55) — higher than the kernel's
         0.40 because LONG is the loss-making side historically.
      3. **Volatility-band filter** — reject if 15m vol is in the
         bottom decile (no fuel) OR top decile (whipsaw risk).
      4. **Spread feasibility** — reject if entry spread > 50% of
         expected gain (slippage would eat the edge).
      5. **Triple-barrier check** — for a LONG at price P with stop S
         and target T, take the trade only if (T - P) / (P - S) >=
         min_reward_risk (default 1.5). I.e., the asymmetric payoff
         must justify the risk.
      6. **Streak filter** — if the last 3 LONG outcomes were all LOSS,
         halve the confidence multiplier (don't fully block — we'd lose
         learning signal — but DOWN-size).

OUTPUT
------
A `MetaLabel` with:
  - `take_trade: bool`            — False ⇒ skip this LONG entirely
  - `confidence_mult: float`      — multiplier applied to the proposal's
                                     confidence before RiskKernel sees it
  - `barrier_hit_prediction: str` — "UPPER" | "LOWER" | "VERTICAL"
                                     (which barrier we expect to hit first)
  - `reason: str`                 — human-readable trace

INTEGRATION
-----------
The PortfolioEngine consults this labeler via an injected attribute.
If `take_trade` is False, the proposal is NOT BUILT (returns None).
If True, the proposal's confidence is multiplied by `confidence_mult`
before Kelly sizing is applied.

This module ONLY filters LONG proposals. SHORT is left alone — the
verifier historically shows SHORT has positive edge, so additional
filtering on SHORT would just reduce sample size without benefit.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.meta_labeler")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # Apply only to LONG (SHORT has positive historical edge, no filter)
    "enabled_for_directions":   ["LONG"],
    # Conviction floor — LONG must clear this to pass
    "long_conviction_floor":    0.55,
    # Volatility band (15m realized vol)
    "vol_floor_pct":            0.003,    # <0.3% = no fuel
    "vol_ceiling_pct":          0.060,    # >6% = whipsaw
    # Spread feasibility
    "max_spread_to_gain_ratio": 0.50,     # spread > 50% of expected gain → reject
    "min_expected_gain_bps":    8.0,      # need at least 8 bps of expected edge
    # Reward/risk (triple barrier asymmetric payoff)
    "min_reward_risk":          1.5,      # (T-P)/(P-S) >= 1.5
    # Streak filter
    "streak_loss_threshold":    3,        # 3 consecutive LONG losses → reduce confidence
    "streak_confidence_mult":   0.50,
    # Trend alignment (soft multiplier)
    "trend_align_bull_mult":    1.10,     # 4h=BULL → +10% confidence
    "trend_align_neutral_mult": 1.00,
    "trend_align_bear_mult":    0.60,     # 4h=BEAR → -40% confidence (don't fully block — primary oracle already does)
    # Sample-size gate: once we have >= 300 verified LONG outcomes, we could
    # train a real ML meta-labeler. For now, the heuristic stays in use.
    "ml_training_threshold":    300,
    # Confidence multiplier floor (don't reduce below this)
    "min_confidence_mult":      0.20,
}


# -------------------- data classes --------------------

@dataclass
class TripleBarrier:
    """The three barriers used to label a LONG trade outcome."""
    entry_price: float
    upper_barrier: float        # take-profit target
    lower_barrier: float        # stop-loss
    vertical_barrier_minutes: int  # time-stop
    # Estimated probabilities of hitting each barrier first (0..1, sum to 1)
    p_upper: float = 0.5
    p_lower: float = 0.4
    p_vertical: float = 0.1
    # Reward/risk ratio
    reward_risk: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def predicted_barrier(self) -> str:
        """Which barrier is most likely to be hit first."""
        if self.p_upper >= self.p_lower and self.p_upper >= self.p_vertical:
            return "UPPER"
        if self.p_lower >= self.p_vertical:
            return "LOWER"
        return "VERTICAL"


@dataclass
class MetaLabel:
    """Output of MetaLabeler.evaluate()."""
    take_trade: bool
    confidence_mult: float
    barrier_hit_prediction: str
    reward_risk: float
    reason: str
    barrier: Optional[TripleBarrier] = None
    rules_checked: int = 0
    rules_passed: int = 0
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# -------------------- Meta-Labeler --------------------

class MetaLabeler:
    """Secondary classifier that filters LONG proposals via triple-barrier logic.

    Usage:
        ml = MetaLabeler()
        # Track LONG outcomes (called from coordinator after each exit):
        ml.record_outcome(direction="LONG", result="WIN")
        # On each proposal:
        label = ml.evaluate(
            direction="LONG",
            conviction=0.62,
            regime_4h="BULL",
            vol_pct=0.012,
            spread_bps=2.5,
            entry_price=1700.0,
            stop_price=1680.0,
            target_price=1740.0,
            expected_ev_bps=12.0,
        )
        if not label.take_trade:
            # Skip this LONG entirely
            return None
        # Apply confidence_mult to the proposal before Kelly sizing
        proposal.confidence *= label.confidence_mult
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        # Per-direction rolling outcome history (for streak detection)
        self._outcomes: dict[str, deque] = {
            "LONG": deque(maxlen=20),
            "SHORT": deque(maxlen=20),
        }
        # Per-direction counters
        self._counts: dict[str, dict[str, int]] = {
            "LONG": {"WIN": 0, "LOSS": 0},
            "SHORT": {"WIN": 0, "LOSS": 0},
        }
        log.info(
            "MetaLabeler init: floor=%.2f vol=[%.3f%%,%.2f%%] min_rr=%.2f streak_thresh=%d",
            self.cfg["long_conviction_floor"],
            self.cfg["vol_floor_pct"] * 100,
            self.cfg["vol_ceiling_pct"] * 100,
            self.cfg["min_reward_risk"],
            self.cfg["streak_loss_threshold"],
        )

    # -------- outcome tracking (called by coordinator) --------

    def record_outcome(self, direction: str, result: str) -> None:
        """Record a trade outcome for streak detection.

        Args:
            direction: "LONG" or "SHORT"
            result: "WIN" or "LOSS"
        """
        direction = direction.upper()
        result = result.upper()
        if direction not in self._outcomes or result not in ("WIN", "LOSS"):
            return
        self._outcomes[direction].append(result)
        self._counts[direction][result] = self._counts[direction].get(result, 0) + 1

    def stats(self) -> dict[str, Any]:
        return {
            "counts": dict(self._counts),
            "streaks": {
                d: self._current_streak(d) for d in ("LONG", "SHORT")
            },
            "ml_training_threshold": self.cfg["ml_training_threshold"],
            "ml_ready": self._counts["LONG"]["WIN"] + self._counts["LONG"]["LOSS"] >= self.cfg["ml_training_threshold"],
        }

    def _current_streak(self, direction: str) -> int:
        """Return the length of the current LOSS streak (0 if last was WIN)."""
        outcomes = list(self._outcomes.get(direction, []))
        if not outcomes or outcomes[-1] != "LOSS":
            return 0
        streak = 0
        for o in reversed(outcomes):
            if o == "LOSS":
                streak += 1
            else:
                break
        return streak

    # -------- evaluation --------

    def evaluate(
        self,
        direction: str,
        conviction: float,
        regime_4h: str,
        vol_pct: float,
        spread_bps: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        expected_ev_bps: float = 0.0,
        time_stop_minutes: int = 60,
    ) -> MetaLabel:
        """Run the meta-labeler heuristic against a proposal.

        Returns a MetaLabel. If `take_trade` is False, the proposal is
        skipped. Otherwise `confidence_mult` should be applied to the
        proposal's confidence before Kelly sizing.
        """
        direction = direction.upper()
        ts = datetime.now(timezone.utc).isoformat()

        # SHORT: pass-through (no meta-labeling)
        if direction not in self.cfg["enabled_for_directions"]:
            return MetaLabel(
                take_trade=True,
                confidence_mult=1.0,
                barrier_hit_prediction="UNKNOWN",
                reward_risk=0.0,
                reason="direction not in enabled_for_directions — pass-through",
                rules_checked=0,
                rules_passed=0,
                ts=ts,
            )

        # LONG: run all 6 rules
        rules_checked = 0
        rules_passed = 0
        reasons: list[str] = []

        # Rule 1: Trend alignment (soft multiplier)
        rules_checked += 1
        trend_mult = self.cfg["trend_align_neutral_mult"]
        if regime_4h == "BULL":
            trend_mult = self.cfg["trend_align_bull_mult"]
        elif regime_4h == "BEAR":
            trend_mult = self.cfg["trend_align_bear_mult"]
        elif regime_4h == "HIGH_VOL":
            trend_mult = self.cfg["trend_align_neutral_mult"] * 0.8
        rules_passed += 1
        reasons.append(f"trend_align({regime_4h})→{trend_mult:.2f}")

        # Rule 2: Conviction floor
        rules_checked += 1
        if conviction < self.cfg["long_conviction_floor"]:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=0.0,
                reason=(
                    f"REJECT long_conviction_floor: {conviction:.3f} < "
                    f"{self.cfg['long_conviction_floor']:.3f}"
                ),
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        rules_passed += 1
        reasons.append(f"conviction({conviction:.3f})≥floor")

        # Rule 3: Volatility band
        rules_checked += 1
        if vol_pct < self.cfg["vol_floor_pct"]:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=0.0,
                reason=f"REJECT vol_floor: {vol_pct*100:.3f}% < {self.cfg['vol_floor_pct']*100:.3f}%",
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        if vol_pct > self.cfg["vol_ceiling_pct"]:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=0.0,
                reason=f"REJECT vol_ceiling: {vol_pct*100:.3f}% > {self.cfg['vol_ceiling_pct']*100:.3f}%",
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        rules_passed += 1
        reasons.append(f"vol({vol_pct*100:.3f}%)∈band")

        # Rule 4: Spread feasibility
        rules_checked += 1
        if expected_ev_bps > 0:
            spread_ratio = spread_bps / expected_ev_bps
            if spread_ratio > self.cfg["max_spread_to_gain_ratio"]:
                return MetaLabel(
                    take_trade=False,
                    confidence_mult=0.0,
                    barrier_hit_prediction="NONE",
                    reward_risk=0.0,
                    reason=(
                        f"REJECT spread_to_gain: {spread_ratio:.2f} > "
                        f"{self.cfg['max_spread_to_gain_ratio']:.2f} "
                        f"(spread={spread_bps:.2f}bps ev={expected_ev_bps:.2f}bps)"
                    ),
                    rules_checked=rules_checked,
                    rules_passed=rules_passed,
                    ts=ts,
                )
            if expected_ev_bps < self.cfg["min_expected_gain_bps"]:
                return MetaLabel(
                    take_trade=False,
                    confidence_mult=0.0,
                    barrier_hit_prediction="NONE",
                    reward_risk=0.0,
                    reason=(
                        f"REJECT ev_too_small: {expected_ev_bps:.2f}bps < "
                        f"{self.cfg['min_expected_gain_bps']:.2f}bps"
                    ),
                    rules_checked=rules_checked,
                    rules_passed=rules_passed,
                    ts=ts,
                )
        rules_passed += 1
        reasons.append(f"spread({spread_bps:.2f}bps)/ev({expected_ev_bps:.2f}bps) OK")

        # Rule 5: Triple-barrier reward/risk
        rules_checked += 1
        if entry_price <= 0 or stop_price <= 0 or target_price <= 0:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=0.0,
                reason=f"REJECT invalid_prices: entry={entry_price} stop={stop_price} target={target_price}",
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        if direction == "LONG":
            reward = target_price - entry_price
            risk = entry_price - stop_price
        else:
            reward = entry_price - target_price
            risk = stop_price - entry_price
        if risk <= 0:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=0.0,
                reason=f"REJECT non_positive_risk: risk={risk:.4f}",
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        rr = reward / risk
        if rr < self.cfg["min_reward_risk"]:
            return MetaLabel(
                take_trade=False,
                confidence_mult=0.0,
                barrier_hit_prediction="NONE",
                reward_risk=rr,
                reason=(
                    f"REJECT reward_risk: {rr:.2f} < {self.cfg['min_reward_risk']:.2f} "
                    f"(reward={reward:.4f} risk={risk:.4f})"
                ),
                rules_checked=rules_checked,
                rules_passed=rules_passed,
                ts=ts,
            )
        rules_passed += 1
        reasons.append(f"rr={rr:.2f}≥{self.cfg['min_reward_risk']:.2f}")

        # Build triple-barrier object with simple probability estimates
        # P(UPPER) heuristic: scales with reward/risk + conviction
        p_upper = 0.5 + 0.1 * (rr - self.cfg["min_reward_risk"]) + 0.1 * (conviction - 0.5)
        p_upper = max(0.1, min(0.85, p_upper))
        p_lower = 1.0 - p_upper - 0.1
        p_lower = max(0.05, p_lower)
        p_vertical = 1.0 - p_upper - p_lower
        barrier = TripleBarrier(
            entry_price=entry_price,
            upper_barrier=target_price,
            lower_barrier=stop_price,
            vertical_barrier_minutes=time_stop_minutes,
            p_upper=round(p_upper, 3),
            p_lower=round(p_lower, 3),
            p_vertical=round(p_vertical, 3),
            reward_risk=round(rr, 3),
        )

        # Rule 6: Streak filter
        rules_checked += 1
        streak = self._current_streak(direction)
        streak_mult = 1.0
        if streak >= self.cfg["streak_loss_threshold"]:
            streak_mult = self.cfg["streak_confidence_mult"]
            reasons.append(f"streak({streak}L)→{streak_mult:.2f}")
        else:
            reasons.append(f"streak({streak}L)→1.00")
        rules_passed += 1

        # Combine multipliers, clamp to floor
        confidence_mult = trend_mult * streak_mult
        confidence_mult = max(self.cfg["min_confidence_mult"], min(1.5, confidence_mult))

        return MetaLabel(
            take_trade=True,
            confidence_mult=round(confidence_mult, 4),
            barrier_hit_prediction=barrier.predicted_barrier(),
            reward_risk=round(rr, 3),
            reason="PASS: " + " | ".join(reasons),
            barrier=barrier,
            rules_checked=rules_checked,
            rules_passed=rules_passed,
            ts=ts,
        )
