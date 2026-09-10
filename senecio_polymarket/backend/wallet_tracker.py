"""
SENECIO ORACLE — Layer 2C: Wallet Tracker
==========================================
Tracks whale / smart-money wallet activity, maintains per-wallet stats,
and surfaces concentration alerts when a single wallet or coordinated
cluster holds > threshold% of a token's recent flow.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

from .models import WalletAlert, utc_now_iso


@dataclass
class WalletTrackerConfig:
    flow_window: int = 50  # last N events per token
    concentration_threshold_pct: float = 30.0  # one wallet >= 30% of flow → alert
    cluster_threshold_pct: float = 55.0  # top 3 wallets >= 55% → cluster alert


@dataclass
class WalletTracker:
    cfg: WalletTrackerConfig = field(default_factory=WalletTrackerConfig)
    flow: dict[str, Deque[dict]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=WalletTrackerConfig().flow_window)))
    wallet_totals: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))

    def ingest(self, ev: WalletAlert) -> list[WalletAlert]:
        """Ingest a wallet alert, return zero or more derived concentration alerts."""
        sym = ev.symbol or ev.payload.get("token")
        if not sym:
            return []
        size = ev.payload.get("size_usd", 0)
        wallet = ev.payload.get("wallet", "?")
        self.flow[sym].append({"wallet": wallet, "size_usd": size, "ts": ev.ts, "action": ev.payload.get("action")})
        self.wallet_totals[sym][wallet] += size

        derived: list[WalletAlert] = []
        # check single-wallet concentration
        total_flow = sum(e["size_usd"] for e in self.flow[sym])
        if total_flow > 0:
            for w, w_size in self.wallet_totals[sym].items():
                pct = (w_size / total_flow) * 100
                if pct >= self.cfg.concentration_threshold_pct:
                    derived.append(WalletAlert(
                        source="wallet_tracker_derived",
                        symbol=sym,
                        trace_id=f"wt-{sym}-{w[:6]}",
                        payload={
                            "wallet": w,
                            "label": "concentrated_flow",
                            "action": "CONCENTRATION_ALERT",
                            "size_usd": round(w_size, 2),
                            "pct_of_flow": round(pct, 2),
                            "token": sym,
                            "ts": ev.ts,
                            "derived_from": ev.event_id,
                        },
                    ))
                    break
            # cluster check (top 3)
            top3 = sorted(self.wallet_totals[sym].items(), key=lambda x: x[1], reverse=True)[:3]
            top3_pct = (sum(s for _, s in top3) / total_flow) * 100 if total_flow else 0
            if top3_pct >= self.cfg.cluster_threshold_pct:
                derived.append(WalletAlert(
                    source="wallet_tracker_derived",
                    symbol=sym,
                    trace_id=f"wt-cluster-{sym}",
                    payload={
                        "wallet": "CLUSTER_TOP3",
                        "label": "smart_money_cluster",
                        "action": "CLUSTER_ALERT",
                        "size_usd": round(sum(s for _, s in top3), 2),
                        "pct_of_flow": round(top3_pct, 2),
                        "token": sym,
                        "ts": ev.ts,
                        "derived_from": ev.event_id,
                    },
                ))
        return derived

    def stats(self) -> dict:
        return {
            "tokens_tracked": len(self.flow),
            "wallets_tracked": sum(len(v) for v in self.wallet_totals.values()),
        }
