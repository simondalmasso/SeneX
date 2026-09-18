from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


VERDICTS = frozenset({"RETAIN", "REJECT", "INCONCLUSIVE"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExperimentRecord:
    hypothesis_id: str
    timestamp: str
    code_hash: str
    config_hash: str
    source_data: list[str]
    source_timestamp: str | None
    data_freshness: str
    market: str
    condition_id: str | None
    token_id: str | None
    horizon: str
    regime: str
    baseline_definition: str
    candidate_definition: str
    in_sample_window: str | None
    oos_window: str | None
    prospective: bool
    lookahead_check: str
    leakage_check: str
    n: int
    resolved_n: int
    abstentions: int
    p_market: float | None
    p_senex: float | None
    brier: float | None
    logloss: float | None
    ece: float | None
    long_wr: float | None
    short_wr: float | None
    spread: float | None
    fees: float | None
    slippage: float | None
    latency: float | None
    gross_ev: float | None
    net_ev: float | None
    pnl: float | None
    profit_factor: float | None
    max_drawdown: float | None
    uncertainty_interval: str | None
    time_window_stability: str | None
    regime_stability: str | None
    verdict: str
    failure_reason: str | None
    notes: str | None

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"invalid verdict: {self.verdict}")
        if self.n < 0 or self.resolved_n < 0 or self.abstentions < 0:
            raise ValueError("counts must be non-negative")
        if self.resolved_n > self.n:
            raise ValueError("resolved_n cannot exceed n")

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    def record_hash(self) -> str:
        return canonical_hash(self.payload())
