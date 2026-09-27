# GPTrader Hourly PAPER — ORDER086 P5

STATUS=RUNBOOK_FROZEN
TASK_NAME=GPTrader Hourly PAPER
Decision MCP only
MAX_BATCHES_PER_RUN=1
NO_FALLBACK_DATA=true
SCIENTIFIC_SPINE_REQUIRES_CHATGPT=false
PERSIST_BEFORE_SETTLEMENT=true
PAPER_ONLY=true
SIMULATION_ONLY=true
LIVE=false
REAL_ORDERS=0
CAPITAL=0


## Runtime ownership gate

The Decision MCP is valid only with all of these exact deployment invariants:

- instances=1
- autoscaling=off
- uvicorn workers=1
- one persistent GPTrader state root
- exclusive GPTrader root lease acquired at startup

If the root lease cannot be acquired, the runtime must refuse startup. Do not run a second worker or rolling instance against the same state root.

MCP_RUNTIME=FROZEN until exact-head CI and independent review clear activation.
PERSISTENCE=NO until persistent-volume restart proof passes.
TASK_CONNECTED=NO.
READY_FOR_SCHEDULE=NO.

## Tool surface

The scheduled decision task may use only these Decision MCP tools:

1. get_gptrader_health
2. get_prediction_batch
3. get_gptrader_state
4. submit_paper_decisions

No Review MCP, public H011 dashboard, web search, current-price service, exchange/crypto market plugin, outcome service, arbitrary URL, shell, backend dispatch, broker, wallet, signer, or database tool is part of the decision surface.

## One hourly cycle

1. Call get_gptrader_health once.
2. If the MCP is absent or ready != true, emit WAIT_MCP and stop with zero decisions and zero trades.
3. If PAPER/simulation safety is not exact, emit SAFETY_BLOCK and stop with zero decisions and zero trades.
4. Read exactly one unseen batch with get_prediction_batch. Do not paginate again in the same run.
5. Decide TAKE or ABSTAIN only from the sealed T0 packet fields.
6. Never FLIP or override the SENEX direction. Primary sizing remains fixed by the PAPER book.
7. Submit the complete batch exactly once through submit_paper_decisions.
8. The service durably commits decisions before any later settlement/replay reader can run; cursor advances only after the batch is durably applied.
9. Stop.

If there are no unseen packets after the one batch read, stop without fabricating decisions.

## Expected call budget

Per scheduled run:
- health: 1
- prediction batch: 1
- decision submit: 0 or 1

Target maximum when work exists: 3 Decision MCP calls/run, about 72 calls/day at hourly cadence.

## Failure posture

WAIT_MCP means the decision surface is unavailable or not ready. It never authorizes fallback data.
SAFETY_BLOCK means PAPER/simulation invariants are not exact. It never authorizes fallback execution.
A failed submit does not authorize cursor advance, synthetic fills, invented decisions, market-data lookup, or a second batch.

The deterministic P1 baselines, sealed packet collection, and scientific sample accounting do not depend on ChatGPT task availability.

It must not use web search, Review MCP, H011 dashboard state, Binance, Bybit,
CoinGecko, CoinMarketCap, TradingCursor, Exum, current-price tools, settlement
outcomes, wallets, signers, broker APIs, D1, arbitrary URLs, shell execution, or
backend function dispatch.

NO_FALLBACK_DATA=true
MAX_BATCHES_PER_RUN=1
PERSIST_BEFORE_SETTLEMENT=true

## One scheduled cycle

1. Call get_gptrader_health once.
2. If unavailable/not ready: return WAIT_MCP; make 0 decisions and 0 trades.
3. If paper_only/simulation_only/live safety fields are not exactly safe:
   return SAFETY_BLOCK; make 0 decisions and 0 trades.
4. Call get_prediction_batch once for one bounded unseen batch.
5. For every eligible packet, choose TAKE or ABSTAIN only. Direction is inherited
   from the sealed SENEX packet and cannot be overridden.
6. Call submit_paper_decisions once with the whole bounded batch.
7. Stop. Do not request another page in the same scheduled cycle.

The durable decision commit must precede any later replay/settlement read.
A retry must use the same idempotency material; conflicting retries fail closed.
Cursor advancement occurs only after durable decision commit and PAPER apply.

## Call budget

Target cadence: approximately 24 runs/day.

Per run:
- get_gptrader_health: <=1
- get_prediction_batch: <=1
- submit_paper_decisions: <=1

Target total: approximately 72 Decision MCP calls/day.
Direct GPTrader D1 reads=0.
Direct GPTrader D1 writes=0.

WAIT_MCP and SAFETY_BLOCK are valid no-action outcomes, not reasons to fabricate
packets, decisions, prices, outcomes, or trades.
