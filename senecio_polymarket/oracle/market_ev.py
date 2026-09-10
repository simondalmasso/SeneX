"""
Module: market_ev.py — MARKET-ANCHORED EXPECTED VALUE

Purpose: Replace "model EV" with "market EV" by anchoring the Expected Value
computation to REAL market frictions:

1. Execution slippage correction — real fill data reduces theoretical EV
2. Orderbook impact correction — thin books increase cost
3. Latency decay correction — stale signals lose edge over time

This solves ⚠️2: "EV sigue siendo modelo interno no calibrado al mercado real"

Before:
    EV = sigmoid(edge) * atr * entropy_discount
    → Pure model, no connection to execution reality

After:
    model_ev = sigmoid(edge) * atr * entropy_discount
    market_ev = model_ev - slippage_cost - impact_cost - latency_decay
    → Anchored to what actually happens when you execute

The SDC's compute_ev() still exists, but now it calls this module's
compute_market_ev() which applies market corrections.

Design principle: NEVER let model_ev exceed what's actually achievable
after execution costs. If the market says your edge is smaller than
your model thinks, the market wins.
"""

import math
import time
from collections import deque
from typing import Optional


class MarketEV:
    """Anchors Expected Value to real market frictions.

    Tracks three correction factors that reduce model EV:

    1. SLIPPAGE CORRECTION
       Tracks realized slippage from past fills.
       If avg_slippage > 0.05% (0.0005), EV is reduced proportionally.
       Uses a rolling window of recent executions.

    2. ORDERBOOK IMPACT CORRECTION
       Estimates market impact from orderbook depth.
       Thin books = higher impact = lower EV.
       If depth is unavailable, uses ATR-based heuristic.

    3. LATENCY DECAY CORRECTION
       Models how signal edge decays over time.
       Edge is not static — it has a half-life.
       After N seconds, edge is discounted by e^(-t/halflife).
    """

    def __init__(
        self,
        slippage_window: int = 50,
        slippage_penalty_factor: float = 5.0,
        impact_cost_bps: float = 2.0,
        edge_half_life_seconds: float = 300.0,
        default_depth_usdt: float = 500000.0,
    ):
        """Initialize MarketEV.

        Args:
            slippage_window: Number of recent fills to track for slippage avg.
            slippage_penalty_factor: Multiplier for slippage impact on EV.
            impact_cost_bps: Base market impact in basis points (2 bps = 0.02%).
            edge_half_life_seconds: Edge decays by 50% every this many seconds.
            default_depth_usdt: Default orderbook depth when unavailable.
        """
        self.slippage_window = slippage_window
        self.slippage_penalty_factor = slippage_penalty_factor
        self.impact_cost_bps = impact_cost_bps
        self.edge_half_life_seconds = edge_half_life_seconds
        self.default_depth_usdt = default_depth_usdt

        # Rolling slippage history (realized fills)
        self._slippage_history = deque(maxlen=slippage_window)

        # Last signal timestamp for latency decay
        self._last_signal_time = None

    # ── SLIPPAGE CORRECTION ─────────────────────────────────────────

    def record_fill_slippage(self, slippage_pct: float):
        """Record realized slippage from a fill.

        Args:
            slippage_pct: Slippage as percentage of price (e.g., 0.0003 = 0.03%).
        """
        self._slippage_history.append(abs(slippage_pct))

    def compute_slippage_correction(self) -> float:
        """Compute EV correction from realized slippage.

        Returns:
            Float representing the EV reduction from slippage.
            Higher slippage → larger correction → lower net EV.

        Formula:
            avg_slippage = mean of recent fills
            correction = avg_slippage * slippage_penalty_factor
            correction = max(0, correction)  # never negative
        """
        if not self._slippage_history:
            # No fill data yet — assume minimal slippage
            return 0.0002  # 0.02% default estimate

        avg_slippage = sum(self._slippage_history) / len(self._slippage_history)
        correction = avg_slippage * self.slippage_penalty_factor
        return max(0.0, correction)

    # ── ORDERBOOK IMPACT CORRECTION ──────────────────────────────────

    def compute_impact_correction(
        self,
        position_usdt: float,
        orderbook_depth_usdt: Optional[float] = None,
    ) -> float:
        """Compute EV correction from estimated market impact.

        Market impact increases with position size relative to orderbook depth.
        Thin books = higher impact = more EV reduction.

        Args:
            position_usdt: Size of the position in USDT.
            orderbook_depth_usdt: Available liquidity within 0.5% of mid.
                                  If None, uses default.

        Returns:
            Float representing the EV reduction from market impact.

        Formula:
            depth = orderbook_depth_usdt or default
            participation_rate = position_usdt / depth
            impact_bps = impact_cost_bps * (1 + participation_rate * 10)
            correction = impact_bps / 10000
        """
        depth = orderbook_depth_usdt or self.default_depth_usdt
        if depth <= 0:
            depth = self.default_depth_usdt

        participation_rate = min(1.0, position_usdt / depth)

        # Impact scales with participation rate (square root model is standard)
        # But we use a more conservative linear model for safety
        impact_bps = self.impact_cost_bps * (1.0 + participation_rate * 10.0)
        correction = impact_bps / 10000.0

        return max(0.0, correction)

    # ── LATENCY DECAY CORRECTION ─────────────────────────────────────

    def set_signal_time(self, timestamp_ms: Optional[int] = None):
        """Record when the signal was generated.

        Call this when a new signal is produced, before compute_market_ev().
        If not called, assumes signal is fresh (no decay).
        """
        self._last_signal_time = timestamp_ms or int(time.time() * 1000)

    def compute_latency_decay(self, edge: float) -> float:
        """Compute edge decay from signal latency.

        Edge is not permanent. As time passes, the information advantage
        decays. This models that reality.

        Args:
            edge: The raw model edge value.

        Returns:
            Decay factor (0-1) to multiply edge by.
            1.0 = fresh signal, 0.5 = half-life reached, ~0 = stale.

        Formula:
            elapsed = now - signal_time
            decay = e^(-elapsed / half_life)
        """
        if self._last_signal_time is None:
            return 1.0  # No signal time set = assume fresh

        now_ms = int(time.time() * 1000)
        elapsed_seconds = (now_ms - self._last_signal_time) / 1000.0

        if elapsed_seconds < 0:
            return 1.0  # Future timestamp = no decay

        decay = math.exp(-elapsed_seconds / self.edge_half_life_seconds)
        return max(0.0, min(1.0, decay))

    # ── MARKET EV COMPUTATION ─────────────────────────────────────────

    def compute_market_ev(
        self,
        model_ev: float,
        edge: float,
        position_usdt: float = 1000.0,
        orderbook_depth_usdt: Optional[float] = None,
        atr_pct: float = 0.02,
    ) -> dict:
        """Compute market-anchored EV from model EV.

        This is THE function. It takes the theoretical model EV and
        applies all three corrections to produce a realistic EV.

        market_ev = model_ev
                    - slippage_correction
                    - impact_correction
                    * latency_decay_factor

        The market_ev can NEVER exceed model_ev. Reality only reduces EV.

        Args:
            model_ev: The theoretical EV from SDC's compute_ev().
            edge: The raw edge value.
            position_usdt: Position size in USDT.
            orderbook_depth_usdt: Orderbook depth within 0.5% of mid.
            atr_pct: ATR as percentage (for context).

        Returns:
            Dict with:
                model_ev: Original model EV
                market_ev: Corrected market-anchored EV
                slippage_correction: EV lost to execution slippage
                impact_correction: EV lost to market impact
                latency_decay: Edge decay factor from latency
                net_correction: Total correction applied
                corrections_applied: List of correction names
        """
        corrections_applied = []

        # 1. Slippage correction (subtractive)
        slippage_corr = self.compute_slippage_correction()
        corrections_applied.append("slippage")

        # 2. Impact correction (subtractive)
        impact_corr = self.compute_impact_correction(position_usdt, orderbook_depth_usdt)
        corrections_applied.append("impact")

        # 3. Latency decay (multiplicative on edge)
        latency_decay = self.compute_latency_decay(edge)
        corrections_applied.append("latency_decay")

        # Apply corrections
        # First apply latency decay to model_ev
        latency_adjusted_ev = model_ev * latency_decay

        # Then subtract execution costs
        total_subtractive = slippage_corr + impact_corr
        market_ev = latency_adjusted_ev - total_subtractive

        # Market EV can never exceed model EV (reality only reduces)
        market_ev = min(market_ev, model_ev)

        # Market EV should reflect edge direction
        # If model_ev is negative, corrections shouldn't make it positive
        if model_ev <= 0:
            market_ev = min(market_ev, model_ev)

        return {
            "model_ev": round(model_ev, 8),
            "market_ev": round(market_ev, 8),
            "slippage_correction": round(slippage_corr, 8),
            "impact_correction": round(impact_corr, 8),
            "latency_decay": round(latency_decay, 6),
            "net_correction": round(total_subtractive, 8),
            "corrections_applied": corrections_applied,
            "position_usdt": position_usdt,
            "orderbook_depth_usdt": orderbook_depth_usdt,
        }


# ---------------------------------------------------------------------------
# Integration helper — drop-in replacement for SDC's compute_ev
# ---------------------------------------------------------------------------

def compute_market_ev(
    edge: float,
    entropy: float,
    atr_pct: float = 0.02,
    market_ev_instance: Optional[MarketEV] = None,
    position_usdt: float = 1000.0,
    orderbook_depth_usdt: Optional[float] = None,
) -> float:
    """Drop-in replacement for SDC's compute_ev that adds market anchoring.

    If no MarketEV instance is provided, falls back to pure model EV
    (backward compatible).

    Args:
        edge: Signal edge from fusion.
        entropy: Signal entropy.
        atr_pct: ATR as percentage.
        market_ev_instance: Optional MarketEV instance for corrections.
        position_usdt: Position size in USDT.
        orderbook_depth_usdt: Available orderbook depth.

    Returns:
        Market-anchored Expected Value (float).
    """
    # Compute model EV (same formula as SDC's compute_ev)
    p_win = 1.0 / (1.0 + math.exp(-edge))
    avg_win = atr_pct * 1.2
    avg_loss = atr_pct * 0.8
    entropy_discount = 1.0 - (entropy * 0.5)
    model_ev = ((p_win * avg_win) - ((1 - p_win) * avg_loss)) * entropy_discount

    # If no MarketEV instance, return pure model EV
    if market_ev_instance is None:
        return model_ev

    # Apply market corrections
    result = market_ev_instance.compute_market_ev(
        model_ev=model_ev,
        edge=edge,
        position_usdt=position_usdt,
        orderbook_depth_usdt=orderbook_depth_usdt,
        atr_pct=atr_pct,
    )

    return result["market_ev"]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("market_ev.py — Self-Test (Market-Anchored EV)")
    print("=" * 60)

    # Test 1: Model EV without market corrections
    print("\n[Test 1] Model EV without market instance...")
    ev_pure = compute_market_ev(edge=0.8, entropy=0.1, atr_pct=0.02)
    assert ev_pure > 0, f"Positive edge should give positive model EV, got {ev_pure}"
    print(f"  model_ev(edge=0.8, entropy=0.1) = {ev_pure:.6f}")
    print(f"  ✓ Pure model EV computed correctly")

    # Test 2: Market EV with corrections
    print("\n[Test 2] Market EV with corrections...")
    mev = MarketEV()
    ev_market = compute_market_ev(
        edge=0.8, entropy=0.1, atr_pct=0.02,
        market_ev_instance=mev,
        position_usdt=2500.0,
    )
    assert ev_market <= ev_pure, f"Market EV should be <= model EV, got {ev_market:.6f} > {ev_pure:.6f}"
    print(f"  model_ev = {ev_pure:.6f}")
    print(f"  market_ev = {ev_market:.6f}")
    print(f"  reduction = {ev_pure - ev_market:.6f}")
    print(f"  ✓ Market corrections reduce EV")

    # Test 3: Slippage correction with history
    print("\n[Test 3] Slippage correction with fill history...")
    mev2 = MarketEV(slippage_window=10, slippage_penalty_factor=5.0)
    # Record some fills with high slippage
    for _ in range(5):
        mev2.record_fill_slippage(0.001)  # 0.1% slippage
    corr = mev2.compute_slippage_correction()
    assert corr > 0, f"Slippage correction should be positive, got {corr}"
    print(f"  avg_slippage=0.001, correction = {corr:.6f}")

    # Now compute market EV with high slippage
    result = mev2.compute_market_ev(
        model_ev=0.01, edge=0.5, position_usdt=2500.0, atr_pct=0.02,
    )
    assert result["market_ev"] < result["model_ev"], "High slippage should reduce EV"
    print(f"  model_ev={result['model_ev']:.6f}, market_ev={result['market_ev']:.6f}")
    print(f"  slippage_correction={result['slippage_correction']:.6f}")
    print(f"  ✓ High slippage reduces EV")

    # Test 4: Orderbook impact correction
    print("\n[Test 4] Orderbook impact correction...")
    mev3 = MarketEV(default_depth_usdt=500000.0)
    # Small position = small impact
    small_impact = mev3.compute_impact_correction(500.0, orderbook_depth_usdt=500000.0)
    # Large position relative to depth = large impact
    large_impact = mev3.compute_impact_correction(50000.0, orderbook_depth_usdt=500000.0)
    # Very thin book = huge impact
    thin_impact = mev3.compute_impact_correction(5000.0, orderbook_depth_usdt=50000.0)
    assert large_impact > small_impact, "Large position should have more impact"
    assert thin_impact > small_impact, "Thin book should have more impact"
    print(f"  small_pos={small_impact:.6f}, large_pos={large_impact:.6f}, thin_book={thin_impact:.6f}")
    print(f"  ✓ Impact scales with position and inversely with depth")

    # Test 5: Latency decay
    print("\n[Test 5] Latency decay...")
    mev4 = MarketEV(edge_half_life_seconds=60.0)  # 60 second half-life
    # Fresh signal (set just now)
    mev4.set_signal_time()
    fresh_decay = mev4.compute_latency_decay(0.5)
    assert fresh_decay > 0.9, f"Fresh signal should have decay ~1.0, got {fresh_decay}"
    print(f"  Fresh signal: decay={fresh_decay:.4f}")

    # Stale signal (5 minutes ago = 5 half-lives = ~3% remaining)
    stale_time = int(time.time() * 1000) - (300 * 1000)  # 5 minutes ago
    mev4.set_signal_time(stale_time)
    stale_decay = mev4.compute_latency_decay(0.5)
    assert stale_decay < 0.1, f"5-minute-old signal should be mostly decayed, got {stale_decay}"
    print(f"  5-min old signal: decay={stale_decay:.4f}")
    print(f"  ✓ Latency decay works correctly")

    # Test 6: Full pipeline with all corrections
    print("\n[Test 6] Full pipeline with all corrections...")
    mev5 = MarketEV(edge_half_life_seconds=120.0)
    # Record some slippage
    mev5.record_fill_slippage(0.0003)
    mev5.record_fill_slippage(0.0005)
    # Set signal as slightly stale (30 seconds)
    mev5.set_signal_time(int(time.time() * 1000) - 30000)
    result = mev5.compute_market_ev(
        model_ev=0.008,
        edge=0.5,
        position_usdt=3000.0,
        orderbook_depth_usdt=200000.0,
        atr_pct=0.02,
    )
    assert result["market_ev"] < result["model_ev"]
    assert result["market_ev"] >= result["model_ev"] - 0.01  # Not too aggressive
    print(f"  model_ev={result['model_ev']:.6f}")
    print(f"  market_ev={result['market_ev']:.6f}")
    print(f"  slippage={result['slippage_correction']:.6f}")
    print(f"  impact={result['impact_correction']:.6f}")
    print(f"  latency_decay={result['latency_decay']:.4f}")
    print(f"  corrections={result['corrections_applied']}")
    print(f"  ✓ Full pipeline produces realistic market EV")

    # Test 7: Negative model EV stays negative
    print("\n[Test 7] Negative model EV stays negative...")
    mev6 = MarketEV()
    result = mev6.compute_market_ev(
        model_ev=-0.005,
        edge=-0.3,
        position_usdt=1000.0,
        atr_pct=0.02,
    )
    assert result["market_ev"] <= 0, f"Negative model EV should stay negative, got {result['market_ev']}"
    print(f"  model_ev={result['model_ev']:.6f}, market_ev={result['market_ev']:.6f}")
    print(f"  ✓ Negative model EV stays negative after corrections")

    # Test 8: Zero slippage history uses default
    print("\n[Test 8] Zero slippage history uses default estimate...")
    mev7 = MarketEV()
    corr = mev7.compute_slippage_correction()
    assert corr == 0.0002, f"Default slippage estimate should be 0.0002, got {corr}"
    print(f"  default_slippage_correction = {corr:.6f}")
    print(f"  ✓ Default slippage estimate works")

    print("\n" + "=" * 60)
    print("All self-tests PASSED ✓")
    print("=" * 60)
