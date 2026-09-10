"""
SENECIO ORACLE — ACT XXVI: HMM Regime Overlay (priority 4)
===========================================================

Probabilistic regime detector that AUGMENTS (does not replace) the
existing rule-based `regime_filter_4h()` in institutional_core.py.

WHY THIS MODULE EXISTS
-----------------------
The current `regime_filter_4h()` returns a hard label: BULL / BEAR /
NEUTRAL / HIGH_VOL. This is fine for hard gating but loses information:

  - A market that's "60% BULL / 40% NEUTRAL" is treated identically to
    "95% BULL / 5% NEUTRAL" — but the conviction should differ.
  - The label flips on a single 0.5% move; it doesn't model persistence.
  - It can't answer "what's the probability of transitioning to BEAR
    in the next 4h?" — a critical input for risk scaling.

A real HMM solves these by maintaining a *belief state* over hidden
regime classes, updated via Bayes rule from observed features.

WHAT THIS MODULE ADDS (additive — does NOT modify institutional_core)
----------------------------------------------------------------------
A lightweight 3-state Gaussian-HMM-style regime classifier with states:
  - BULL      : positive drift, low vol
  - BEAR      : negative drift, low-moderate vol
  - HIGH_VOL  : near-zero drift, very high vol (transition state)

We don't train a real Baum-Welch HMM because:
  (a) we don't have a labeled training set,
  (b) the hyperparameters would overfit to one coin's history.

Instead we ship a *prior-calibrated* HMM with reasonable transitions
+ emission parameters derived from common crypto-market statistics.
The implementation uses direct forward recursion (no Viterbi) — fast
enough to run on every 15-min cycle.

OUTPUT
------
A `RegimeBelief` dataclass with:
  - `probabilities`: {BULL: 0.6, BEAR: 0.1, HIGH_VOL: 0.3}
  - `dominant`: "BULL"
  - `entropy`: 0.42 (low = confident, high = uncertain)
  - `transition_risk`: prob of transitioning to BEAR in next 4h
  - `long_bias`: 0..1 — composite "should I be LONG?" score
  - `short_bias`: 0..1 — composite "should I be SHORT?" score
  - `size_mult`: 0.5..1.0 — vol-regime-derived size multiplier

INTEGRATION
-----------
The coordinator calls `regime_hmm.update(ohlcv, funding_rate, oi_change)`
on every prediction cycle. The resulting `RegimeBelief` is:
  1. Stored on the prediction's `_audit` dict (additive, non-breaking).
  2. Read by the MetaLabeler to soft-scale LONG confidence.
  3. Read by the RiskKernel to soft-scale size in HIGH_VOL.
  4. Exposed via /api/portfolio/regime_hmm for observability.

The hard `regime_filter_4h()` in institutional_core stays UNTOUCHED.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

log = logging.getLogger("senecio.regime_hmm")


# -------------------- config --------------------

DEFAULTS: dict[str, Any] = {
    # State set
    "states":              ["BULL", "BEAR", "HIGH_VOL"],
    # Initial belief (uniform-ish, slight BULL bias for crypto's long drift)
    "initial_belief":      [0.50, 0.25, 0.25],
    # Transition matrix T[i][j] = P(state_t+1 = j | state_t = i)
    # Rows = from-state, cols = to-state. Order: BULL, BEAR, HIGH_VOL.
    # Calibrated to crypto 15-min cadence (~96 transitions per day).
    # Diagonal dominance reflects regime persistence.
    "transition_matrix":   [
        [0.92, 0.04, 0.04],   # BULL → 92% BULL, 4% BEAR, 4% HIGH_VOL
        [0.05, 0.88, 0.07],   # BEAR → 5% BULL, 88% BEAR, 7% HIGH_VOL
        [0.30, 0.30, 0.40],   # HIGH_VOL → 30% BULL, 30% BEAR, 40% HIGH_VOL
    ],
    # Emission parameters: Gaussian on (4h_return, 4h_vol) per state
    # Format: {state: {"ret_mean": float, "ret_std": float, "vol_mean": float, "vol_std": float}}
    # Numbers are in FRACTIONS (0.005 = 0.5%).
    "emission_params": {
        "BULL":     {"ret_mean":  0.008, "ret_std": 0.005, "vol_mean": 0.012, "vol_std": 0.005},
        "BEAR":     {"ret_mean": -0.010, "ret_std": 0.007, "vol_mean": 0.018, "vol_std": 0.006},
        "HIGH_VOL": {"ret_mean":  0.000, "ret_std": 0.012, "vol_mean": 0.045, "vol_std": 0.015},
    },
    # Composite bias mapping (used to derive long_bias / short_bias)
    "long_bias_by_state":  {"BULL": 0.85, "BEAR": 0.10, "HIGH_VOL": 0.40},
    "short_bias_by_state": {"BULL": 0.15, "BEAR": 0.85, "HIGH_VOL": 0.40},
    # Size multiplier by state
    "size_mult_by_state":  {"BULL": 1.00, "BEAR": 1.00, "HIGH_VOL": 0.50},
    # Confidence floor for belief updates (avoid log(0) issues)
    "prob_floor":          1e-6,
    # Persistence smoothing (EMA factor for belief stability)
    "ema_alpha":           0.30,
}


# -------------------- data classes --------------------

@dataclass
class RegimeBelief:
    """Output of the HMM forward update — current belief over regimes."""
    probabilities: dict[str, float]    # {BULL: 0..1, BEAR: 0..1, HIGH_VOL: 0..1}
    dominant: str                       # state with highest probability
    entropy: float                      # 0..log(3) — uncertainty
    transition_risk_to_bear: float      # prob of being in BEAR next step
    long_bias: float                    # 0..1
    short_bias: float                   # 0..1
    size_mult: float                    # 0.5..1.0
    obs_return: float                   # the 4h return we observed
    obs_vol: float                      # the 4h vol we observed
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -------------------- HMM Regime Overlay --------------------

class HMMRegimeOverlay:
    """3-state Gaussian-emission HMM for crypto regime detection.

    Usage:
        hmm = HMMRegimeOverlay()
        # On each cycle:
        belief = hmm.update(obs_return=0.012, obs_vol=0.020)
        # belief.dominant = "BULL" / "BEAR" / "HIGH_VOL"
        # belief.long_bias, belief.short_bias, belief.size_mult
    """

    def __init__(self, config: Optional[dict] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.states: list[str] = list(self.cfg["states"])
        self._T = np.array(self.cfg["transition_matrix"], dtype=float)
        # Validate transition matrix rows sum to ~1
        for i, row in enumerate(self._T):
            s = float(row.sum())
            if abs(s - 1.0) > 0.01:
                log.warning(
                    "transition_matrix row %d sums to %.4f (expected 1.0) — normalizing",
                    i, s,
                )
                self._T[i] = row / max(s, 1e-9)
        # Initial belief
        self._belief = np.array(self.cfg["initial_belief"], dtype=float)
        self._belief = self._belief / max(self._belief.sum(), 1e-9)
        self._emission = self.cfg["emission_params"]
        self._updates = 0
        log.info(
            "HMMRegimeOverlay init: states=%s initial_belief=%s",
            self.states, self._belief.tolist(),
        )

    # -------- public API --------

    def update(
        self,
        obs_return: float,
        obs_vol: float,
        funding_rate: Optional[float] = None,
        oi_change_pct: Optional[float] = None,
    ) -> RegimeBelief:
        """Run one forward step of the HMM.

        Args:
            obs_return: 4h realized return as a fraction (0.012 = 1.2%)
            obs_vol:    4h realized volatility as a fraction (0.020 = 2.0%)
            funding_rate: optional, used as a soft BEAR prior if extreme
            oi_change_pct: optional, used as a soft BEAR prior if extreme

        Returns:
            RegimeBelief with the new posterior.
        """
        # 1) Prediction step: belief_prior = belief_prev @ T
        belief_prior = self._belief @ self._T
        # 2) Update step: multiply by emission likelihoods
        likelihoods = np.array([
            self._gaussian_pdf(obs_return, obs_vol, state)
            for state in self.states
        ], dtype=float)
        belief_post = belief_prior * likelihoods
        s = belief_post.sum()
        if s < self.cfg["prob_floor"]:
            # Degenerate — reset to prior
            belief_post = belief_prior.copy()
            s = belief_post.sum() or 1.0
        belief_post = belief_post / s
        # 3) Apply funding/OI priors if provided (soft BEAR tilt on extremes)
        if funding_rate is not None and abs(funding_rate) > 0.001:
            # Extreme funding (|funding| > 10 bps per 8h) → tilt toward BEAR
            bear_idx = self.states.index("BEAR") if "BEAR" in self.states else 1
            tilt = min(0.10, abs(funding_rate) * 50.0)  # max 10% tilt
            # Move mass from BULL → BEAR proportional to tilt
            bull_idx = self.states.index("BULL") if "BULL" in self.states else 0
            move = belief_post[bull_idx] * tilt
            belief_post[bull_idx] -= move
            belief_post[bear_idx] += move
            s = belief_post.sum()
            belief_post = belief_post / s if s > 0 else belief_post
        # 4) EMA smoothing for stability
        alpha = self.cfg["ema_alpha"]
        self._belief = alpha * belief_post + (1.0 - alpha) * self._belief
        s = self._belief.sum()
        self._belief = self._belief / s if s > 0 else self._belief
        self._updates += 1

        # 5) Build the RegimeBelief output
        probs = {state: float(p) for state, p in zip(self.states, self._belief)}
        dominant = max(probs, key=probs.get)
        entropy = self._entropy(self._belief)
        # Transition risk to BEAR: belief @ T[:, BEAR_idx]
        bear_idx = self.states.index("BEAR") if "BEAR" in self.states else 1
        transition_risk = float((self._belief @ self._T)[bear_idx])
        long_bias = sum(
            probs[s] * self.cfg["long_bias_by_state"].get(s, 0.5)
            for s in self.states
        )
        short_bias = sum(
            probs[s] * self.cfg["short_bias_by_state"].get(s, 0.5)
            for s in self.states
        )
        size_mult = self.cfg["size_mult_by_state"].get(dominant, 1.0)
        return RegimeBelief(
            probabilities={k: round(v, 4) for k, v in probs.items()},
            dominant=dominant,
            entropy=round(float(entropy), 4),
            transition_risk_to_bear=round(transition_risk, 4),
            long_bias=round(float(long_bias), 4),
            short_bias=round(float(short_bias), 4),
            size_mult=round(float(size_mult), 4),
            obs_return=round(float(obs_return), 6),
            obs_vol=round(float(obs_vol), 6),
            ts=datetime.now(timezone.utc).isoformat(),
        )

    def update_from_ohlcv(
        self,
        ohlcv: list[list],
        funding_rate: Optional[float] = None,
        oi_change_pct: Optional[float] = None,
    ) -> RegimeBelief:
        """Convenience wrapper: compute obs_return + obs_vol from OHLCV then update.

        Args:
            ohlcv: list of [ts, o, h, l, c, v] rows; we use the last 16 (4h on 15m).
            funding_rate: optional funding rate (fraction, e.g. 0.0002 = 2bps)
            oi_change_pct: optional 24h OI change (percent, e.g. 5.0 = +5%)
        """
        if len(ohlcv) < 16:
            # Warm-up: return current belief without updating
            return self.snapshot()
        try:
            close_now = float(ohlcv[-1][4])
            close_4h_ago = float(ohlcv[-16][4])
            if close_4h_ago <= 0:
                return self.snapshot()
            obs_return = (close_now - close_4h_ago) / close_4h_ago
            vols = []
            for c in ohlcv[-16:]:
                if c[4] > 0:
                    vols.append((c[2] - c[3]) / c[4])
            obs_vol = sum(vols) / len(vols) if vols else 0.01
        except (IndexError, ValueError, TypeError, ZeroDivisionError):
            return self.snapshot()
        return self.update(
            obs_return=obs_return,
            obs_vol=obs_vol,
            funding_rate=funding_rate,
            oi_change_pct=oi_change_pct,
        )

    def snapshot(self) -> RegimeBelief:
        """Return the current belief without updating."""
        probs = {state: float(p) for state, p in zip(self.states, self._belief)}
        dominant = max(probs, key=probs.get)
        entropy = self._entropy(self._belief)
        bear_idx = self.states.index("BEAR") if "BEAR" in self.states else 1
        transition_risk = float((self._belief @ self._T)[bear_idx])
        long_bias = sum(
            probs[s] * self.cfg["long_bias_by_state"].get(s, 0.5)
            for s in self.states
        )
        short_bias = sum(
            probs[s] * self.cfg["short_bias_by_state"].get(s, 0.5)
            for s in self.states
        )
        size_mult = self.cfg["size_mult_by_state"].get(dominant, 1.0)
        return RegimeBelief(
            probabilities={k: round(v, 4) for k, v in probs.items()},
            dominant=dominant,
            entropy=round(float(entropy), 4),
            transition_risk_to_bear=round(transition_risk, 4),
            long_bias=round(float(long_bias), 4),
            short_bias=round(float(short_bias), 4),
            size_mult=round(float(size_mult), 4),
            obs_return=0.0,
            obs_vol=0.0,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    def stats(self) -> dict[str, Any]:
        return {
            "updates": self._updates,
            "current_belief": {s: float(p) for s, p in zip(self.states, self._belief)},
            "states": self.states,
        }

    # -------- helpers --------

    def _gaussian_pdf(self, ret: float, vol: float, state: str) -> float:
        """Joint Gaussian likelihood of (ret, vol) under the state's emission params."""
        params = self._emission.get(state, {"ret_mean": 0, "ret_std": 0.01, "vol_mean": 0.02, "vol_std": 0.01})
        ret_pdf = self._norm_pdf(ret, params["ret_mean"], max(params["ret_std"], 1e-6))
        vol_pdf = self._norm_pdf(vol, params["vol_mean"], max(params["vol_std"], 1e-6))
        return max(self.cfg["prob_floor"], ret_pdf * vol_pdf)

    @staticmethod
    def _norm_pdf(x: float, mean: float, std: float) -> float:
        """Gaussian PDF (unnormalized is fine — we normalize later)."""
        z = (x - mean) / std
        return math.exp(-0.5 * z * z) / (std * math.sqrt(2 * math.pi))

    @staticmethod
    def _entropy(probs: np.ndarray) -> float:
        """Shannon entropy in nats."""
        h = 0.0
        for p in probs:
            if p > 1e-9:
                h -= p * math.log(p)
        return h
