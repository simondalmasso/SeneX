# ORDER085 Owner Review — 2026-09-26

STATUS=APPROVED_WITH_CORRECTIONS  
ARCHITECTURE=HYBRID_C_SEALED_PACKET  
IMPLEMENTATION_AUTHORIZED=NO  
NEXT_IMPLEMENTATION_ORDER=ORDER086  
SOURCE_EXPORT_SHA256=6f2840da23b62717bc2f9f4f887712e65b3e7e98bb2f6003d53f600243d87fd9

## Decisions

OD-01 — The scientific spine must work without ChatGPT MCP writes. Deterministic sealing, baselines, replay and verdict remain autonomous.

OD-02 — Prefer a dedicated Northflank service/sidecar after capability preflight. If isolation/persistence is unsafe or unavailable, use a dedicated Cloudflare Worker with **no D1 binding**. Never mount mutating MCP on public H011.

OD-03 — FRESH_ENTRY is outside v1.

OD-04 — No operative freshness parameter in v1. A 120-second window is only a candidate for a future separately authorized FRESH_ENTRY order.

OD-05 — Strong verdicts require `N_resolved_independent_1h >= 600` and `calendar_days >= 14`; otherwise `INSUFFICIENT_DATA`.

## Scientific corrections

1. Distinguish `SENEX_DIRECTION_ANTI_INFORMATIVE` from `SENEX_SIGNAL_NOT_USEFUL`; v1 cannot FLIP.
2. Treat `up_prob` and confidence as uncalibrated scores. No proper probability scoring or calibrated-probability claim on raw values.
3. Primary inference must handle temporal dependence: hour-cluster paired BTC/ETH observations and preregister a temporal block/bootstrap or equivalent method. Wilson is descriptive only.
4. Primary ChatGPT treatment is fixed-risk TAKE/ABSTAIN. SIZE_SCALE is secondary exploratory only.
5. Persist the immutable GPTrader decision before any replay/settlement consumes later evidence, even if that evidence already exists at task time.
6. The scheduled decision task gets Decision MCP only; no web, Review MCP, dashboard, exchange/crypto plugins, current-price or outcome tools.

PAPER_ONLY=true  
LIVE=NO  
REAL_ORDERS=0  
CAPITAL=0  
D1_WRITES=0  
DEPLOYMENTS=0
