"""
ACT-XXIX — Module 7: Market Simulation, Chaos Engineering & Fault Injection
============================================================================

Generates synthetic market data, adversarial scenarios, and injects faults
into the system to test its robustness. Includes time-travel replay engine.

Public surface
--------------
- ``SyntheticMarketGenerator``   — GBM + jumps + regime-switching OHLCV
- ``ScenarioGenerator``          — parametric scenario builder
- ``AdversarialMarketSimulator`` — worst-case stress generator
- ``RegimeTransitionSimulator``  — simulate BULL→BEAR→CRASH transitions
- ``Fault``                      — base fault
- ``FaultInjector``              — fault injection framework
- ``ExchangeFailureSimulator``   — outage / partial fill / rejected orders
- ``NetworkDegradationSimulator``— latency / packet loss / desync
- ``APIInconsistencySimulator``  — stale quotes / wrong symbols
- ``ClockSkewSimulator``         — time jumps / drift
- ``TimeTravelReplayEngine``     — replay historical ticks
"""
from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Iterator

import numpy as np


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _now_ts() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# SyntheticMarketGenerator
# ---------------------------------------------------------------------------

@dataclass
class OHLCVBar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return asdict(self)


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOL = "HIGH_VOL"
    CRASH = "CRASH"


class SyntheticMarketGenerator:
    """Generates synthetic OHLCV bars using:
      - Geometric Brownian Motion (GBM) as base
      - Jump diffusion (Merton model) for tail events
      - Markov regime switching (BULL/BEAR/SIDEWAYS/HIGH_VOL/CRASH)

    Configurable per-bar:
      - base_drift (per bar)
      - base_volatility (per bar)
      - jump_lambda (jumps per bar)
      - jump_mean (log-mean of jump size)
      - jump_std (log-std of jump size)
    """

    REGIME_PARAMS = {
        Regime.BULL:     {"drift":  0.0008, "vol": 0.010, "jump_lambda": 0.01},
        Regime.BEAR:     {"drift": -0.0006, "vol": 0.015, "jump_lambda": 0.02},
        Regime.SIDEWAYS: {"drift":  0.0000, "vol": 0.008, "jump_lambda": 0.005},
        Regime.HIGH_VOL: {"drift":  0.0002, "vol": 0.030, "jump_lambda": 0.04},
        Regime.CRASH:    {"drift": -0.0050, "vol": 0.050, "jump_lambda": 0.10},
    }

    REGIME_TRANSITIONS = {
        Regime.BULL:     {Regime.BULL: 0.95, Regime.BEAR: 0.02, Regime.SIDEWAYS: 0.02, Regime.HIGH_VOL: 0.01, Regime.CRASH: 0.00},
        Regime.BEAR:     {Regime.BULL: 0.05, Regime.BEAR: 0.85, Regime.SIDEWAYS: 0.07, Regime.HIGH_VOL: 0.02, Regime.CRASH: 0.01},
        Regime.SIDEWAYS: {Regime.BULL: 0.10, Regime.BEAR: 0.05, Regime.SIDEWAYS: 0.80, Regime.HIGH_VOL: 0.05, Regime.CRASH: 0.00},
        Regime.HIGH_VOL: {Regime.BULL: 0.15, Regime.BEAR: 0.10, Regime.SIDEWAYS: 0.10, Regime.HIGH_VOL: 0.60, Regime.CRASH: 0.05},
        Regime.CRASH:    {Regime.BULL: 0.30, Regime.BEAR: 0.40, Regime.SIDEWAYS: 0.20, Regime.HIGH_VOL: 0.05, Regime.CRASH: 0.05},
    }

    def __init__(self, initial_price: float = 50000.0,
                 seed: int = 42,
                 base_volume: float = 1000.0,
                 start_regime: Regime = Regime.SIDEWAYS,
                 bar_interval_s: int = 900):
        self.initial_price = initial_price
        self.current_price = initial_price
        self.rng = np.random.default_rng(seed)
        self.base_volume = base_volume
        self.current_regime = start_regime
        self.bar_interval_s = bar_interval_s
        self._bar_count = 0
        self._lock = threading.Lock()

    def _transition_regime(self) -> None:
        probs = self.REGIME_TRANSITIONS[self.current_regime]
        r = self.rng.random()
        cumulative = 0.0
        for regime, p in probs.items():
            cumulative += p
            if r <= cumulative:
                self.current_regime = regime
                return

    def _generate_one_bar(self) -> OHLCVBar:
        params = self.REGIME_PARAMS[self.current_regime]
        drift = params["drift"]
        vol = params["vol"]
        jump_lambda = params["jump_lambda"]
        # GBM with jumps
        n_jumps = self.rng.poisson(jump_lambda)
        jump_component = 0.0
        for _ in range(n_jumps):
            jump_component += self.rng.normal(-0.005, 0.02)  # negative-skew jumps
        ret = drift + vol * self.rng.normal(0, 1) + jump_component
        new_price = max(0.01, self.current_price * (1 + ret))
        # OHLC: open = prev close, close = new_price, high/low with intrabar vol
        open_ = self.current_price
        close = new_price
        intrabar_vol = vol * 0.5
        high = max(open_, close) * (1 + abs(self.rng.normal(0, intrabar_vol)))
        low = min(open_, close) * (1 - abs(self.rng.normal(0, intrabar_vol)))
        volume = self.base_volume * (1 + abs(self.rng.normal(0, 0.3)))
        if self.current_regime == Regime.CRASH:
            volume *= 3.0  # volume spike on crash
        elif self.current_regime == Regime.HIGH_VOL:
            volume *= 2.0
        bar = OHLCVBar(
            ts=_now_iso(),
            open=open_, high=high, low=low, close=close, volume=volume,
        )
        self.current_price = new_price
        self._bar_count += 1
        self._transition_regime()
        return bar

    def generate(self, n_bars: int) -> list[OHLCVBar]:
        """Generate n_bars of OHLCV data."""
        with self._lock:
            bars = [self._generate_one_bar() for _ in range(n_bars)]
            return bars

    def generator(self) -> Iterator[OHLCVBar]:
        """Infinite generator."""
        while True:
            with self._lock:
                yield self._generate_one_bar()

    def reset(self, initial_price: float | None = None,
              regime: Regime | None = None) -> None:
        with self._lock:
            self.current_price = initial_price or self.initial_price
            self.current_regime = regime or self.current_regime
            self._bar_count = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "bar_count": self._bar_count,
                "current_price": self.current_price,
                "current_regime": self.current_regime.value,
            }


# ---------------------------------------------------------------------------
# ScenarioGenerator — parametric scenarios
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    description: str
    bars: list[OHLCVBar]
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "bar_count": len(self.bars),
            "metadata": self.metadata,
            "first_bar": self.bars[0].to_dict() if self.bars else None,
            "last_bar": self.bars[-1].to_dict() if self.bars else None,
        }


class ScenarioGenerator:
    """Builds named scenarios by composing simple shocks onto a base generator.

    Pre-defined scenarios:
      - 'flash_crash'    — sudden -10% drop in 5 bars
      - 'vol_spike'      — vol × 5 for 20 bars
      - 'liquidity_dry'  — volume × 0.1 for 10 bars
      - 'bull_run'       — steady +0.5%/bar for 50 bars
      - 'bear_rally'     — dead-cat bounce: +3% then -8%
      - 'whipsaw'        — alternating +1%/-1% for 30 bars
      - 'gap_up'         — +5% gap then steady
      - 'gap_down'       — -5% gap then steady
    """

    def __init__(self, base_generator: SyntheticMarketGenerator):
        self.base = base_generator

    def flash_crash(self, magnitude: float = 0.10, n_bars: int = 5) -> Scenario:
        bars = []
        price = self.base.current_price
        for i in range(n_bars):
            ret = -magnitude / n_bars
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * 3,
            ))
            price = new_price
        return Scenario(
            name="flash_crash",
            description=f"{magnitude*100:.1f}% drop in {n_bars} bars",
            bars=bars,
            metadata={"magnitude": magnitude, "n_bars": n_bars},
        )

    def vol_spike(self, multiplier: float = 5.0, n_bars: int = 20) -> Scenario:
        bars = []
        price = self.base.current_price
        for i in range(n_bars):
            ret = self.base.rng.normal(0, 0.01 * multiplier)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * 2,
            ))
            price = new_price
        return Scenario(
            name="vol_spike",
            description=f"vol × {multiplier} for {n_bars} bars",
            bars=bars,
            metadata={"multiplier": multiplier, "n_bars": n_bars},
        )

    def liquidity_dry(self, vol_multiplier: float = 0.1,
                     n_bars: int = 10) -> Scenario:
        bars = self.base.generate(n_bars)
        for b in bars:
            b.volume *= vol_multiplier
        return Scenario(
            name="liquidity_dry",
            description=f"volume × {vol_multiplier} for {n_bars} bars",
            bars=bars,
            metadata={"vol_multiplier": vol_multiplier, "n_bars": n_bars},
        )

    def bull_run(self, per_bar_ret: float = 0.005,
                n_bars: int = 50) -> Scenario:
        bars = []
        price = self.base.current_price
        for i in range(n_bars):
            noise = self.base.rng.normal(0, 0.003)
            ret = per_bar_ret + noise
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume,
            ))
            price = new_price
        return Scenario(
            name="bull_run",
            description=f"+{per_bar_ret*100:.2f}%/bar for {n_bars} bars",
            bars=bars,
            metadata={"per_bar_ret": per_bar_ret, "n_bars": n_bars},
        )

    def whipsaw(self, n_bars: int = 30, magnitude: float = 0.01) -> Scenario:
        bars = []
        price = self.base.current_price
        for i in range(n_bars):
            ret = magnitude if i % 2 == 0 else -magnitude
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * 1.5,
            ))
            price = new_price
        return Scenario(
            name="whipsaw",
            description=f"±{magnitude*100:.1f}% alternating for {n_bars} bars",
            bars=bars,
            metadata={"magnitude": magnitude, "n_bars": n_bars},
        )

    def gap(self, direction: str = "up", magnitude: float = 0.05,
            n_bars: int = 20) -> Scenario:
        """direction: 'up' or 'down'."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        sign = 1 if direction == "up" else -1
        bars = []
        price = self.base.current_price
        # Gap bar
        gap_price = price * (1 + sign * magnitude)
        bars.append(OHLCVBar(
            ts=_now_iso(),
            open=price, high=max(price, gap_price),
            low=min(price, gap_price), close=gap_price,
            volume=self.base.base_volume * 4,
        ))
        price = gap_price
        # Steady bars
        for i in range(n_bars - 1):
            ret = self.base.rng.normal(0, 0.003)
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume,
            ))
            price = new_price
        return Scenario(
            name=f"gap_{direction}",
            description=f"{direction} gap {magnitude*100:.1f}% then {n_bars-1} bars",
            bars=bars,
            metadata={"direction": direction, "magnitude": magnitude,
                      "n_bars": n_bars},
        )

    def all_scenarios(self) -> dict[str, Scenario]:
        return {
            "flash_crash": self.flash_crash(),
            "vol_spike": self.vol_spike(),
            "liquidity_dry": self.liquidity_dry(),
            "bull_run": self.bull_run(),
            "whipsaw": self.whipsaw(),
            "gap_up": self.gap("up"),
            "gap_down": self.gap("down"),
        }


# ---------------------------------------------------------------------------
# AdversarialMarketSimulator
# ---------------------------------------------------------------------------

class AdversarialMarketSimulator:
    """Generates adversarial market conditions designed to break strategies.

    Strategies:
      - 'max_drawdown' — find the path that maximises DD for a given strategy
      - 'whipsaw_extreme' — perfectly wrong-direction every bar
      - 'tail_event' — once-in-10-year shock
      - 'correlation_breakdown' — correlations flip suddenly
    """

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)

    def max_drawdown_path(self, starting_price: float = 50000.0,
                          n_bars: int = 100,
                          max_bar_loss: float = 0.02) -> list[OHLCVBar]:
        """Worst-case path: every bar down by ``max_bar_loss``."""
        bars = []
        price = starting_price
        for i in range(n_bars):
            new_price = price * (1 - max_bar_loss)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=price, low=new_price, close=new_price,
                volume=1000,
            ))
            price = new_price
        return bars

    def whipsaw_extreme(self, starting_price: float = 50000.0,
                       n_bars: int = 50,
                       magnitude: float = 0.02) -> list[OHLCVBar]:
        """Alternate up/down by magnitude — designed to trigger stop-losses."""
        bars = []
        price = starting_price
        for i in range(n_bars):
            ret = magnitude if i % 2 == 0 else -magnitude
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=2000,
            ))
            price = new_price
        return bars

    def tail_event(self, starting_price: float = 50000.0,
                  magnitude: float = 0.20,
                  direction: str = "down") -> list[OHLCVBar]:
        """Single bar with extreme move (e.g. -20% flash crash)."""
        sign = -1 if direction == "down" else 1
        new_price = starting_price * (1 + sign * magnitude)
        return [OHLCVBar(
            ts=_now_iso(),
            open=starting_price,
            high=max(starting_price, new_price),
            low=min(starting_price, new_price),
            close=new_price,
            volume=50000,  # massive volume
        )]


# ---------------------------------------------------------------------------
# RegimeTransitionSimulator
# ---------------------------------------------------------------------------

class RegimeTransitionSimulator:
    """Simulates regime transitions to test strategy adaptiveness.

    Pre-defined transition sequences:
      - 'bull_to_bear'    — gradual transition over N bars
      - 'crash_recovery'  — sharp drop then slow recovery
      - 'volatility_cycle' — calm → vol spike → calm
    """

    def __init__(self, base_generator: SyntheticMarketGenerator):
        self.base = base_generator

    def bull_to_bear(self, n_bars: int = 60) -> list[OHLCVBar]:
        """Smoothly transition BULL → BEAR over n_bars."""
        bars = []
        price = self.base.current_price
        for i in range(n_bars):
            t = i / n_bars
            # Drift interpolates from +0.08%/bar (bull) to -0.06%/bar (bear)
            drift = 0.0008 * (1 - t) + (-0.0006) * t
            # Vol increases mid-transition
            vol = 0.010 + 0.010 * math.sin(t * math.pi)
            ret = drift + vol * self.base.rng.normal(0, 1)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * (1 + t),
            ))
            price = new_price
        return bars

    def crash_recovery(self, crash_magnitude: float = 0.15,
                      n_crash_bars: int = 5,
                      n_recovery_bars: int = 80) -> list[OHLCVBar]:
        """Sharp crash then slow recovery."""
        bars = []
        price = self.base.current_price
        # Crash phase
        for i in range(n_crash_bars):
            ret = -crash_magnitude / n_crash_bars
            new_price = price * (1 + ret)
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=price, low=new_price, close=new_price,
                volume=self.base.base_volume * 4,
            ))
            price = new_price
        # Recovery phase
        for i in range(n_recovery_bars):
            ret = 0.0008 + 0.015 * self.base.rng.normal(0, 1)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * 1.5,
            ))
            price = new_price
        return bars

    def volatility_cycle(self, n_calm: int = 30,
                        n_spike: int = 20,
                        n_calm2: int = 30) -> list[OHLCVBar]:
        """Calm → vol spike → calm."""
        bars = []
        price = self.base.current_price
        # Calm phase
        for i in range(n_calm):
            ret = self.base.rng.normal(0, 0.005)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume,
            ))
            price = new_price
        # Spike phase
        for i in range(n_spike):
            ret = self.base.rng.normal(0, 0.030)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume * 2,
            ))
            price = new_price
        # Calm phase 2
        for i in range(n_calm2):
            ret = self.base.rng.normal(0, 0.005)
            new_price = max(0.01, price * (1 + ret))
            bars.append(OHLCVBar(
                ts=_now_iso(),
                open=price, high=max(price, new_price),
                low=min(price, new_price), close=new_price,
                volume=self.base.base_volume,
            ))
            price = new_price
        return bars


# ---------------------------------------------------------------------------
# Fault injection framework
# ---------------------------------------------------------------------------

@dataclass
class Fault:
    """A single fault to inject."""
    name: str
    kind: str          # "exchange_failure" / "network" / "api" / "clock"
    severity: str      # "low" / "medium" / "high" / "critical"
    description: str
    trigger: dict      # parameters controlling when/how the fault fires
    duration_s: float = 1.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class FaultInjector:
    """Framework for orchestrating fault injection.

    Usage:
        fi = FaultInjector()
        fi.schedule_fault(Fault(name='exch_outage_1', kind='exchange_failure', ...))
        fi.start()  # background scheduler
        # ... later ...
        active_faults = fi.active_faults()
        fi.stop()
    """

    def __init__(self):
        self._scheduled: list[tuple[float, Fault]] = []  # (fire_ts, fault)
        self._active: list[tuple[Fault, float]] = []    # (fault, expire_ts)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._history: deque[dict] = deque(maxlen=500)

    def schedule_fault(self, fault: Fault, delay_s: float = 0.0) -> None:
        with self._lock:
            fire_ts = _now_ts() + delay_s
            self._scheduled.append((fire_ts, fault))
            self._scheduled.sort(key=lambda x: x[0])

    def clear_schedule(self) -> None:
        with self._lock:
            self._scheduled.clear()
            self._active.clear()

    def start(self, poll_interval_s: float = 0.1) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(poll_interval_s,),
            name="fault_injector", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self, poll_interval_s: float) -> None:
        while not self._stop.is_set():
            now = _now_ts()
            # Fire due scheduled faults
            with self._lock:
                still_scheduled = []
                for fire_ts, fault in self._scheduled:
                    if fire_ts <= now:
                        self._active.append((fault, now + fault.duration_s))
                        self._history.append({
                            "ts": _now_iso(), "action": "activated",
                            "fault": fault.to_dict(),
                        })
                    else:
                        still_scheduled.append((fire_ts, fault))
                self._scheduled = still_scheduled
                # Expire old faults
                still_active = []
                for fault, exp in self._active:
                    if exp > now:
                        still_active.append((fault, exp))
                    else:
                        self._history.append({
                            "ts": _now_iso(), "action": "expired",
                            "fault": fault.to_dict(),
                        })
                self._active = still_active
            self._stop.wait(poll_interval_s)

    def active_faults(self) -> list[Fault]:
        with self._lock:
            return [f for f, _ in self._active]

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._history)[-limit:]

    def is_fault_active(self, kind: str) -> bool:
        with self._lock:
            return any(f.kind == kind for f, _ in self._active)


# ---------------------------------------------------------------------------
# Concrete fault simulators
# ---------------------------------------------------------------------------

class ExchangeFailureSimulator:
    """Pre-defined exchange failure faults."""

    @staticmethod
    def outage(duration_s: float = 30.0, exchange: str = "binance") -> Fault:
        return Fault(
            name=f"outage_{exchange}_{int(duration_s)}s",
            kind="exchange_failure",
            severity="critical",
            description=f"Exchange {exchange} completely unavailable",
            trigger={"type": "outage", "exchange": exchange},
            duration_s=duration_s,
        )

    @staticmethod
    def partial_fill_rate(rate: float = 0.5, duration_s: float = 60.0) -> Fault:
        return Fault(
            name=f"partial_fill_{int(rate*100)}pct",
            kind="exchange_failure",
            severity="high",
            description=f"Partial fill rate jumps to {rate*100:.0f}%",
            trigger={"type": "partial_fill_rate", "rate": rate},
            duration_s=duration_s,
        )

    @staticmethod
    def rejected_orders(rate: float = 0.3, duration_s: float = 60.0) -> Fault:
        return Fault(
            name=f"rejected_{int(rate*100)}pct",
            kind="exchange_failure",
            severity="high",
            description=f"{rate*100:.0f}% of orders rejected",
            trigger={"type": "rejected_rate", "rate": rate},
            duration_s=duration_s,
        )


class NetworkDegradationSimulator:
    """Pre-defined network degradation faults."""

    @staticmethod
    def latency_spike(latency_ms: float = 2000.0,
                     duration_s: float = 60.0) -> Fault:
        return Fault(
            name=f"latency_{int(latency_ms)}ms",
            kind="network",
            severity="high",
            description=f"Network latency +{latency_ms}ms",
            trigger={"type": "latency_ms", "latency_ms": latency_ms},
            duration_s=duration_s,
        )

    @staticmethod
    def packet_loss(rate: float = 0.05, duration_s: float = 60.0) -> Fault:
        return Fault(
            name=f"loss_{int(rate*100)}pct",
            kind="network",
            severity="medium",
            description=f"{rate*100:.1f}% packet loss",
            trigger={"type": "packet_loss", "rate": rate},
            duration_s=duration_s,
        )

    @staticmethod
    def desync(duration_s: float = 30.0) -> Fault:
        return Fault(
            name="desync",
            kind="network",
            severity="critical",
            description="Clock desync between client and exchange",
            trigger={"type": "desync", "offset_ms": 1500},
            duration_s=duration_s,
        )


class APIInconsistencySimulator:
    """Pre-defined API inconsistency faults."""

    @staticmethod
    def stale_quotes(duration_s: float = 30.0) -> Fault:
        return Fault(
            name="stale_quotes",
            kind="api",
            severity="high",
            description="API returns stale quotes (last_price frozen)",
            trigger={"type": "stale_quotes"},
            duration_s=duration_s,
        )

    @staticmethod
    def wrong_symbol(duration_s: float = 10.0) -> Fault:
        return Fault(
            name="wrong_symbol",
            kind="api",
            severity="medium",
            description="API returns data for wrong symbol",
            trigger={"type": "wrong_symbol", "expected": "BTC/USDT",
                     "actual": "BTS/USDT"},
            duration_s=duration_s,
        )

    @staticmethod
    def schema_drift(duration_s: float = 30.0) -> Fault:
        return Fault(
            name="schema_drift",
            kind="api",
            severity="medium",
            description="API changes response schema (key rename)",
            trigger={"type": "schema_drift",
                     "old_key": "last_price", "new_key": "lastPrice"},
            duration_s=duration_s,
        )


class ClockSkewSimulator:
    """Pre-defined clock-skew faults."""

    @staticmethod
    def time_jump(seconds: float = 60.0) -> Fault:
        return Fault(
            name=f"time_jump_{int(seconds)}s",
            kind="clock",
            severity="high",
            description=f"Clock jumps {seconds}s forward",
            trigger={"type": "time_jump", "seconds": seconds},
            duration_s=1.0,
        )

    @staticmethod
    def drift(ppm: float = 1000.0, duration_s: float = 600.0) -> Fault:
        """ppm = parts per million drift rate."""
        return Fault(
            name=f"drift_{int(ppm)}ppm",
            kind="clock",
            severity="low",
            description=f"Clock drifts {ppm}ppm",
            trigger={"type": "drift", "ppm": ppm},
            duration_s=duration_s,
        )


# ---------------------------------------------------------------------------
# TimeTravelReplayEngine
# ---------------------------------------------------------------------------

class TimeTravelReplayEngine:
    """Replays historical ticks/bars at controlled speed.

    Usage:
        tt = TimeTravelReplayEngine()
        tt.load_history(historical_bars)  # list of OHLCVBar or dicts
        # Replay at 1x speed (real-time) starting from bar 0
        for bar in tt.replay(start_idx=0, speed=1.0):
            process(bar)
        # Or replay at 100x speed (compressed)
        for bar in tt.replay(start_idx=0, speed=100.0):
            process(bar)
    """

    def __init__(self, default_interval_s: float = 900.0):
        self.default_interval_s = default_interval_s
        self._history: list[OHLCVBar] = []
        self._lock = threading.Lock()

    def load_history(self, bars: list[OHLCVBar] | list[dict]) -> None:
        with self._lock:
            self._history = []
            for b in bars:
                if isinstance(b, OHLCVBar):
                    self._history.append(b)
                elif isinstance(b, dict):
                    self._history.append(OHLCVBar(
                        ts=b.get("ts", _now_iso()),
                        open=b["open"], high=b["high"],
                        low=b["low"], close=b["close"],
                        volume=b.get("volume", 0.0),
                    ))
                else:
                    raise TypeError(f"unsupported bar type: {type(b)}")

    def replay(self, start_idx: int = 0, end_idx: int | None = None,
               speed: float = 1.0,
               sleep: bool = True) -> Iterator[OHLCVBar]:
        """Yield bars from start_idx to end_idx (exclusive).

        If sleep=True and speed > 0, sleeps between bars to simulate real time.
        If speed=0, no sleep (instant replay).
        """
        with self._lock:
            history = list(self._history)
        if end_idx is None:
            end_idx = len(history)
        if start_idx < 0 or start_idx >= len(history):
            return
        if end_idx <= start_idx:
            return
        for i in range(start_idx, min(end_idx, len(history))):
            bar = history[i]
            yield bar
            if sleep and speed > 0:
                time.sleep(self.default_interval_s / speed)

    def replay_window(self, start_ts: str, end_ts: str,
                     speed: float = 1.0,
                     sleep: bool = True) -> Iterator[OHLCVBar]:
        """Replay by timestamp range."""
        with self._lock:
            history = list(self._history)
        in_window = [b for b in history if start_ts <= b.ts <= end_ts]
        for bar in in_window:
            yield bar
            if sleep and speed > 0:
                time.sleep(self.default_interval_s / speed)

    def count(self) -> int:
        with self._lock:
            return len(self._history)

    def time_range(self) -> tuple[str, str] | None:
        with self._lock:
            if not self._history:
                return None
            return (self._history[0].ts, self._history[-1].ts)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "OHLCVBar",
    "Regime",
    "SyntheticMarketGenerator",
    "Scenario",
    "ScenarioGenerator",
    "AdversarialMarketSimulator",
    "RegimeTransitionSimulator",
    "Fault",
    "FaultInjector",
    "ExchangeFailureSimulator",
    "NetworkDegradationSimulator",
    "APIInconsistencySimulator",
    "ClockSkewSimulator",
    "TimeTravelReplayEngine",
]
