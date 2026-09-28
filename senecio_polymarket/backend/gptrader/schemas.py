from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "gptrader.t0.v1"
PACKET_ID_PREFIX = "gpt0-"

TOP_LEVEL_ALLOWLIST = (
    "timestamp",
    "symbol",
    "prediction",
    "confidence",
    "ev",
    "price_now",
    "exchange_used",
    "candle_ts",
)

AUDIT_ALLOWLIST = (
    "origin_price_v1",
    "confidence_semantics_v1",
    "execution_state",
    "external_markets_v1",
    "decision_replay_v1",
    "canonical_ev_contract_v1",
)

PIPELINE_ALLOWLIST = (
    "step2_features",
    "step4_ev",
)

# Exact names that can contain information unavailable at prediction time.
# Null/empty placeholders are tolerated at source so freshly-created Oracle
# rows can be sealed, but these fields are never copied into a packet.
OUTCOME_FUTURE_KEYS = frozenset(
    {
        "outcome",
        "outcome_15m",
        "outcome_1h",
        "outcomes_dual",
        "price_15m_later",
        "price_1h_later",
        "future_price",
        "future_prices",
        "post_t0_price",
        "post_prediction_price",
        "current_price",
        "settled",
        "settled_at",
        "settlement",
        "settlement_proof",
        "settlement_proof_v1",
        "settlement_cas",
        "resolution_price",
        "resolved_at",
        "realized_pnl",
        "realized_pnl_usd",
        "unrealized_pnl",
        "unrealized_pnl_usd",
        "pnl",
        "profit",
        "review_mcp",
        "review_payload",
    }
)


def has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
