# ORDER093-AUD — GPTrader DIRECT PAPER $0 activation

ISSUER=AUD  
EXECUTOR=GPTrader chat only  
OWNER_COST_USD=0  
STATUS=ACTIVE_HANDOFF  
PAPER_ONLY=true  
SIMULATION_ONLY=true  
LIVE=false  
REAL_ORDERS=0  
CAPITAL=0  
D1_READS=0  
D1_WRITES=0

## 1. Objective

Start collecting real GPTrader treatment decisions now, at zero incremental cost, by consuming the existing SENEX H011 prediction surface directly.

This order explicitly removes the dedicated Decision MCP deployment as a prerequisite for the zero-cost treatment lane.

AUD does **not** execute GPTrader decisions. The GPTrader chat owns TAKE/ABSTAIN decisions and its own policy. AUD only defines the data boundary, safety invariants, evidence contract, and stop conditions.

## 2. Canonical direct source

Use only:

`GET https://h011-web--senecio-h011--wbjggn89fnf8.code.run/api/oracle/predictions/db?limit=4&symbol=BTCUSDT`

This is the only decision-data source for this lane.

Do not query Binance, Bybit, CoinGecko, CoinMarketCap, Exum/ExoScope, CryptoAudit, TradingCursor, TraderSpy, LONA, current-price services, search engines, dashboards, settlement APIs, D1, broker APIs, wallets, signers, or arbitrary URLs while generating a decision.

## 3. Required source gates

Before deciding anything, require all of the following from the H011 response:

- `bounded=true`
- `degraded=false`
- `fresh=true`
- `provenance.exact=true`
- `safety.trade_mode=PAPER`
- `safety.orders_enabled=false`
- `safety.live_capital_locked=true`
- `safety.hard_paper_lock=true`

If any field is absent, contradictory, stale, degraded, or unsafe:

`STATUS=SAFETY_BLOCK`

and stop with zero new decisions.

## 4. No-lookahead projection

A candidate row is decision-eligible only if it is still pre-outcome at read time.

Reject the row if any of these are non-null/present with resolved information:

- `outcome`
- `outcome_15m`
- `outcome_1h`
- `price_15m_later`
- `price_1h_later`
- `audit.outcomes_dual`
- settlement/resolution/review/future-price fields of any kind

For an eligible row, expose to GPTrader only this allowlist:

- `id`
- `ts`
- `symbol`
- `prediction`
- `confidence`
- `ev` when present
- `price_now` strictly as the prediction-time origin price
- `exchange_used`
- `audit.origin_price_v1`

Everything else is hidden from decision generation.

The fact that a forbidden field exists must never be converted into a signal.

## 5. GPTrader decision authority

GPTrader decides exactly one of:

- `TAKE`
- `ABSTAIN`

for each unseen eligible row.

Rules:

- GPTrader owns the decision policy; AUD does not substitute a threshold or strategy.
- `TAKE` inherits the SENEX direction exactly.
- Never FLIP LONG↔SHORT.
- `FLAT` cannot become a directional TAKE.
- Missing, ambiguous, malformed, or insufficient decision-time evidence must fail closed to ABSTAIN or row rejection under GPTrader's own protocol.
- No current market state or later evidence may be consulted before the complete batch is fixed.

## 6. Durable $0 ledger

Use GitHub Issue #91 in `simondalmasso/SeneX` as the append-only direct-treatment ledger:

`https://github.com/simondalmasso/SeneX/issues/91`

Before each run:

1. Read all existing `GPTRADER_DIRECT_RECEIPT` comments.
2. Collect all previously recorded H011 prediction IDs.
3. Skip those IDs idempotently.

After all decisions for the run are fixed, append exactly one comment beginning:

`GPTRADER_DIRECT_RECEIPT`

The receipt must contain:

- `run_id`
- source endpoint
- H011 `provenance.source_commit`
- H011 authority snapshot id/generation when available
- for each row: `prediction_id`, `ts`, inherited SENEX direction, GPTrader action, GPTrader reason codes
- `PAPER_ONLY=true`
- `SIMULATION_ONLY=true`
- `LIVE=false`
- `REAL_ORDERS=0`
- `CAPITAL=0`
- `D1_READS=0`
- `D1_WRITES=0`

Do not write later prices, outcomes, settlement results, or review data into the decision receipt.

If the GitHub append fails:

`STATUS=PERSISTENCE_FAIL`

Do not claim the batch durable and do not process another batch.

## 7. Treatment semantics

This lane is a PAPER scientific treatment lane.

A `TAKE` means a hypothetical GPTrader position based on the decision-time SENEX packet. It does not create an exchange order, broker order, wallet action, capital movement, or live trade.

The receipt timestamp establishes the information cut.

Outcome review/settlement may happen only after the receipt is durable and is a separate phase. Later outcome data must never alter or regenerate the original decision.

This direct ledger is authoritative for ORDER093 treatment decisions. It is intentionally independent of the H011 local GPTrader dashboard ledger until a separate zero-cost integration is explicitly approved and verified.

## 8. Existing task ownership

There must remain exactly one scheduled task named:

`GPTrader Hourly PAPER`

AUD must not impersonate GPTrader or silently rewrite that task again.

The GPTrader chat may update its own existing task to implement this order. It must not create a second GPTrader scheduler.

Suggested cadence remains hourly unless GPTrader deliberately chooses another existing-task cadence.

Maximum per run: one bounded H011 fetch window and one durable receipt.

## 9. External-tool firewall

The following may be used only outside the decision-generation information cut, for static QA, reproducibility, or later AUD review:

- Wolfram
- QA Wolf
- Binance
- Exum / ExoScope Crypto
- CryptoAudit
- TradingCursor
- TraderSpy
- LONA Trading Assistant

Rules:

- none may supply TAKE/ABSTAIN inputs;
- none may provide current price, technical indicators, order books, market sentiment, or forecasts before the decision receipt;
- none may change a durable GPTrader decision;
- any later comparison must be clearly labeled post-decision audit evidence.

## 10. Zero-cost rule

No new Northflank service.  
No new Northflank volume.  
No paid API.  
No paid model fallback.  
No cloud upgrade.  
No billing authorization.

If execution requires incremental spend:

`STATUS=BLOCK_REAL_COST`

and stop.

## 11. Stop conditions

Valid immediate stops:

- `SAFETY_BLOCK`
- `SOURCE_UNAVAILABLE`
- `NO_ELIGIBLE_PRE_OUTCOME_ROWS`
- `PERSISTENCE_FAIL`
- `BLOCK_REAL_COST`

Do not backfill a missed decision after its outcome/later-price evidence has become available.

## 12. Required GPTrader return

Return compactly:

```text
NUM_ORDER=ORDER093-AUD
STATUS=COMPLETE|SAFETY_BLOCK|SOURCE_UNAVAILABLE|NO_ELIGIBLE_PRE_OUTCOME_ROWS|PERSISTENCE_FAIL|BLOCK_REAL_COST
RUN_ID=
H011_SOURCE_COMMIT=
AUTHORITY_SNAPSHOT_ID=
ROWS_READ=
ROWS_ELIGIBLE=
ROWS_POST_T0_REJECTED=
ROWS_DUPLICATE_SKIPPED=
TAKE_COUNT=
ABSTAIN_COUNT=
LEDGER_ISSUE=91
LEDGER_RECEIPT_URL=
PAPER_ONLY=true
SIMULATION_ONLY=true
LIVE=false
REAL_ORDERS=0
CAPITAL=0
D1_READS=0
D1_WRITES=0
OWNER_COST_USD=0
```

## 13. AUD handoff

GPTrader: execute this order prospectively. Do not ask AUD to make the trading decisions. If the direct source and safety gates pass, begin producing durable PAPER treatment decisions immediately. If they do not pass, return the exact blocker without substituting another data source.
