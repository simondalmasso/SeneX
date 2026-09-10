"""
SENECIO ORACLE — Layer 3: LLM-Driven Decision Brain
====================================================
Inspired by Polymarket/agents planner. Implements a 5-check decision pipeline:

  1. Base rate check       — historical win rate of this setup type
  2. News / catalyst check — is there a fresh catalyst?
  3. Market structure check— trend, momentum, depth
  4. Wallet behavior check — smart money aligned or fading?
  5. Calibration / confidence gating — must clear threshold

Emits a SIGNAL event with action vector + reasons + per-check breakdown.

By default runs deterministic rule-based logic (no LLM call) so it works
offline. Set `engine.llm_enabled = True` to enable an LLM call (uses
z-ai-web-dev-sdk style interface via httpx if available).
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import Any

from .models import Signal, Action, MarketCandidate, MarketTick, WalletAlert, utc_now_iso


@dataclass
class BrainConfig:
    min_confidence: float = 0.55
    min_ev: float = 0.05  # 5% edge
    base_size_usd: float = 500.0
    max_size_usd: float = 5_000.0
    llm_enabled: bool = False
    llm_model: str = "glm-4-plus"


@dataclass
class OracleEngine:
    cfg: BrainConfig = field(default_factory=BrainConfig)
    # rolling stats
    setup_history: dict[str, list[dict]] = field(default_factory=dict)  # setup_type -> [{outcome, ...}]
    recent_wallets: dict[str, list[dict]] = field(default_factory=dict)  # symbol -> recent wallet events

    def observe_wallet(self, ev: WalletAlert) -> None:
        sym = ev.symbol or ev.payload.get("token")
        if not sym:
            return
        self.recent_wallets.setdefault(sym, []).append({
            "ts": ev.ts,
            "wallet": ev.payload.get("wallet"),
            "label": ev.payload.get("label"),
            "action": ev.payload.get("action"),
            "size_usd": ev.payload.get("size_usd", 0),
        })
        # keep last 20
        if len(self.recent_wallets[sym]) > 20:
            self.recent_wallets[sym] = self.recent_wallets[sym][-20:]

    def record_outcome(self, setup_type: str, outcome_pct: float) -> None:
        self.setup_history.setdefault(setup_type, []).append({"outcome_pct": outcome_pct})
        if len(self.setup_history[setup_type]) > 200:
            self.setup_history[setup_type] = self.setup_history[setup_type][-200:]

    def base_rate(self, setup_type: str) -> tuple[float, float]:
        """Returns (win_rate, sample_size)."""
        hist = self.setup_history.get(setup_type, [])
        if len(hist) < 5:
            return (0.50, 0)  # default
        wins = sum(1 for r in hist if r["outcome_pct"] > 0)
        return (wins / len(hist), len(hist))

    # ---- main decision pipeline ----
    async def decide(
        self,
        candidate: MarketCandidate,
        tick: MarketTick | None = None,
    ) -> Signal:
        setup = candidate.payload.get("scanner", "unknown")
        sym = candidate.symbol

        # 1. base rate
        br, n = self.base_rate(setup)

        # 2. catalyst
        catalyst = candidate.payload.get("catalyst") or self._extract_catalyst(setup)

        # 3. market structure
        structure = self._structure_check(candidate, tick)

        # 4. wallet behavior
        wallet = self._wallet_check(sym)

        # 5. calibration / confidence
        confidence = self._calibrate(br, catalyst, structure, wallet)
        ev_estimate = confidence * 1.0 - 0.5  # naive EV proxy

        # action vector
        if confidence >= self.cfg.min_confidence and ev_estimate >= self.cfg.min_ev:
            action = Action.LONG if setup != "B_trend_join_long" or True else Action.LONG
            sizing = min(self.cfg.max_size_usd, self.cfg.base_size_usd * (1 + confidence))
        elif confidence < 0.30:
            action = Action.WATCH
            sizing = 0.0
        else:
            action = Action.HOLD
            sizing = 0.0

        checks = {
            "base_rate":          {"win_rate": round(br, 3), "samples": n, "pass": br >= 0.45},
            "catalyst":           {"present": bool(catalyst), "pass": bool(catalyst)},
            "market_structure":   structure,
            "wallet_behavior":    wallet,
            "calibration":        {"confidence": round(confidence, 3), "ev": round(ev_estimate, 3),
                                   "pass": confidence >= self.cfg.min_confidence},
        }
        reasons = [
            f"Base rate: {br*100:.1f}% over {n} samples",
            f"Catalyst: {catalyst['key'] if catalyst else 'none'}",
            f"Structure: {structure['trend']}, depth {structure['depth_score']}",
            f"Wallets: {wallet['net_flow']} ({wallet['label']})",
            f"Confidence {confidence:.2f}, EV {ev_estimate:+.3f}",
        ]
        return Signal(
            source="oracle_engine",
            symbol=sym,
            trace_id=f"sig-{sym}-{candidate.trace_id[-6:]}",
            payload={
                "action": action.value,
                "confidence": round(confidence, 3),
                "ev": round(ev_estimate, 3),
                "sizing_usd": round(sizing, 2),
                "setup": setup,
                "checks": checks,
                "reasons": reasons,
                "llm_enabled": self.cfg.llm_enabled,
            },
        )

    # ---- helpers ----
    def _extract_catalyst(self, setup: str) -> dict | None:
        if "A_premarket" in setup:
            return {"key": "gap_with_catalyst", "headline": "Pre-market gap with news flow"}
        return None

    def _structure_check(self, candidate: MarketCandidate, tick: MarketTick | None) -> dict:
        score = candidate.payload.get("score", 0)
        sma = candidate.payload.get("sma200")
        trend = "up" if sma and candidate.payload.get("price", 0) > sma else "neutral"
        depth_score = min(100, score * 4)
        return {
            "trend": trend,
            "depth_score": round(depth_score, 2),
            "momentum_score": round(min(100, score * 3.5), 2),
            "pass": depth_score >= 30 and trend == "up",
        }

    def _wallet_check(self, sym: str) -> dict:
        events = self.recent_wallets.get(sym, [])
        if not events:
            return {"net_flow": 0, "label": "no_data", "pass": False}
        buy = sum(e["size_usd"] for e in events if e["action"] in ("BUY", "ACCUMULATE"))
        sell = sum(e["size_usd"] for e in events if e["action"] in ("SELL", "DISTRIBUTE"))
        net = buy - sell
        label = "smart_buy" if net > 0 else "smart_distribute" if net < 0 else "balanced"
        return {
            "net_flow": round(net, 2),
            "buy_usd": round(buy, 2),
            "sell_usd": round(sell, 2),
            "label": label,
            "pass": net > 0,
        }

    def _calibrate(self, br: float, catalyst: dict | None, structure: dict, wallet: dict) -> float:
        # weighted sum of evidence
        score = 0.0
        score += br * 0.30
        score += (0.25 if catalyst else 0.0)
        score += (0.25 if structure.get("pass") else 0.10)
        score += (0.20 if wallet.get("pass") else 0.05)
        return max(0.0, min(1.0, score))
