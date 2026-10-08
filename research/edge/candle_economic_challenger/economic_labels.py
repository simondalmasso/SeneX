"""Reference 1h net economic returns, never proof of broker fills."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
from .contracts import EvidenceError, digest

@dataclass(frozen=True)
class CostModel:
    fee_bps:float=10.
    spread_bps:float=0.
    slippage_bps:float=5.
    latency_bps:float=0.
    funding_bps:float=0.
    def __post_init__(self):
        if any(not math.isfinite(x) or x<0 for x in asdict(self).values()):
            raise EvidenceError("cost components must be non-negative finite")
    def sha256(self): return digest(asdict(self))
    def total(self): return sum(asdict(self).values())

def economic_trade(side,entry_reference,exit_reference,cost_model,horizon_seconds,notional_usdt=1000.):
    if horizon_seconds!=3600: raise EvidenceError("horizon must be exact 3600s")
    try: entry=float(entry_reference); exit_=float(exit_reference); notional=float(notional_usdt)
    except (ValueError,TypeError) as exc: raise EvidenceError("invalid price or notional") from exc
    if not all(map(math.isfinite,(entry,exit_,notional))) or min(entry,exit_,notional)<=0:
        raise EvidenceError("invalid non-positive, NaN or infinite reference")
    if side not in ("LONG","SHORT","FLAT") or not isinstance(cost_model,CostModel):
        raise EvidenceError("invalid direction/cost contract")
    exposure={"LONG":1,"SHORT":-1,"FLAT":0}[side]
    gross=exposure*(exit_/entry-1)*10000
    breakdown=dict(zip(("fee","spread","slippage","latency","funding"),
                       (cost_model.fee_bps,cost_model.spread_bps,cost_model.slippage_bps,
                        cost_model.latency_bps,cost_model.funding_bps))) if exposure else dict.fromkeys(("fee","spread","slippage","latency","funding"),0.)
    cost=sum(breakdown.values()); net=gross-cost
    return {"side":side,"gross_bps":gross,"cost_bps":cost,"cost_breakdown_bps":breakdown,
            "net_bps":net,"pnl_usdt":notional*net/10000,
            "notional_usdt":notional if exposure else 0.,
            "fill_type":"REFERENCE_PROXY_NOT_EXECUTED"}
