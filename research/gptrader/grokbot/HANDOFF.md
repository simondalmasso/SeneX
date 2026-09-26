# ORDER085 — GROKBOT HANDOFF

Issue: https://github.com/simondalmasso/SeneX/issues/73

## Mission

Design **GPTrader**: a serial, highly efficient, PAPER-only trading agent that consumes SENEX predictions, makes auditable simulated decisions, and generates evidence that can tell us whether SENEX predictions contain useful trading information or are operationally useless.

Do not optimize for a positive answer. A valid terminal design must make it possible to conclude:

`SENEX_SIGNAL_NOT_USEFUL`

when the evidence supports that conclusion.

## Mandatory discovery before design

Reconstruct from current GitHub state, not memory:

1. `oracle_runner` prediction cadence, payload, persistence and settlement path.
2. Existing PAPER flow:
   `oracle_runner -> PortfolioCoordinator -> RiskKernel -> ExecutionEngine -> TradeJournal -> ShadowLive`.
3. Public `/api/paper/*` and dashboard contracts.
4. Local persistence/volume paths and whether GPTrader can stay off D1.
5. Current PAPER/live safety gates.
6. Current production/runtime constraints relevant to a future MCP.

Record measured vs inferred facts in `DESIGN.md`.

## Core architectural question

Compare:

A. AI/trader embedded inside SENEX/H011.  
B. Hourly external ChatGPT task consuming an MCP.  
C. Hybrid: H011 creates sealed prediction batches and owns PAPER simulation; ChatGPT supplies only policy/selection decisions hourly.

Recommend one based on:
- minimum Cloudflare/D1 cost;
- no look-ahead;
- reproducibility;
- restartability;
- auditability;
- safety;
- dashboard integration;
- low operational complexity;
- scientific ability to falsify usefulness.

## Hourly batching target

Nominal current cadence is 15 minutes x 2 symbols:
- ~8 prediction opportunities/hour
- ~192/day
- hourly ChatGPT task = 24 runs/day

Design a stable cursor/batch contract so one hourly read can consume **all unseen packets**, not only the latest prediction. This can reduce external polling calls by about 87.5% versus one poll per prediction.

Do not lose intermediate predictions.

## Look-ahead rule

An hourly agent must not pretend that a prediction generated 45 minutes earlier was acted on live at its original price.

Design both semantics explicitly:

### Calibration/replay
MCP returns sealed decision-time packets that exclude:
- outcomes;
- post-decision prices;
- future candles/orderbook;
- later dashboard state;
- any field derived from settlement.

GPTrader decides only from the sealed packet. PAPER settlement happens separately and truthfully later.

### Fresh-entry
Only packets inside a preregistered freshness window may create a current-time simulated entry. Older packets are calibration/replay-only.

No hidden future leakage.

## MCP contract

Design exact schemas for bounded tools, including:

- `get_prediction_batch(cursor, limit)`
- `get_gptrader_state()`
- `submit_paper_decisions(run_id, cursor, decisions[])`
- `get_gptrader_results(...)`
- `get_gptrader_health()`

Names may change if justified.

Requirements:
- stable cursor;
- bounded batch;
- idempotent run/decision IDs;
- retry-safe;
- explicit provenance/version;
- authenticated PAPER-only mutation;
- no generic backend invocation;
- no real-order path;
- no capital unlock;
- no model mutation;
- no D1 write;
- no arbitrary shell/admin behavior.

The public H011 surface is currently intentionally read-only. Do not casually mount a mutating public `/mcp`. Design a safe authenticated topology.

## Cloudflare/D1 budget

Default target:
- GPTrader steady-state direct D1 reads = 0
- GPTrader D1 writes = 0
- prefer in-memory/local bounded state + durable local volume/journal where feasible
- no OFFSET
- no COUNT(*)
- no broad history scans
- no repeated full hydration
- cache static context
- one bounded batch read + one idempotent PAPER submission per hourly run is the target

Quantify:
- calls/run;
- calls/day;
- max predictions/batch;
- worst-case response bytes;
- restart catch-up behavior.

## Reuse, do not fork execution logic

SENEX already has PAPER execution machinery. Do not propose an ungoverned second fill/risk engine unless you prove reuse is impossible.

Prefer:
- GPTrader owns decision policy;
- existing risk/execution/fill primitives remain canonical;
- GPTrader trades are separately namespaced/journaled for clean A/B comparison.

The existing SENEX PAPER path remains the control.

## Scientific evaluation

Design a paired chronological no-lookahead experiment.

Compare:
1. raw SENEX prediction usefulness;
2. existing SENEX PAPER control;
3. GPTrader treatment;
4. deterministic minimal baselines;
5. market/prior baseline when legitimately available at decision time.

Measure prediction quality and trading performance separately.

Required diagnostics include, where semantically valid:
- directional accuracy;
- calibration/scoring diagnostics;
- confidence/EV reliability;
- LONG/SHORT asymmetry;
- symbol/regime slices with sufficient N;
- stale-signal decay;
- abstention impact.

Trading diagnostics:
- eligible predictions;
- decisions/trades/abstentions;
- net PnL after simulated fees/slippage;
- profit factor;
- max drawdown;
- win rate;
- turnover;
- holding time;
- risk-adjusted metrics only when N supports them.

Design explicit verdict states:
- `SENEX_SIGNAL_USEFUL_EVIDENCE`
- `SENEX_SIGNAL_NOT_USEFUL`
- `GPTRADER_POLICY_BAD_OR_UNPROVEN`
- `EXECUTION_ASSUMPTIONS_DOMINATE`
- `INCREMENTAL_EDGE_NOT_ESTABLISHED`
- `INSUFFICIENT_DATA`
- `INDETERMINATE`

Positive PnL alone never proves EDGE.

## Calibration boundary

ORDER085 may design diagnostics but may not tune SENEX.

The design must show how later evidence could identify:
- confidence over/understatement;
- direction bias;
- regime/symbol failure;
- stale-signal decay;
- GPTrader selection bias;
- useful abstention regions;
- meaningless/miscalibrated confidence or EV fields.

Any actual model/threshold calibration is a separate future owner-approved order with fresh validation.

## Dashboard

Design a separate `GPTrader — PAPER/HYPOTHETICAL` dashboard section, never overwriting existing SENEX paper execution.

Show at least:
- last hourly run;
- cursor/freshness;
- packets processed;
- decisions/trades/abstentions;
- open/closed positions;
- PnL/drawdown;
- SENEX-control comparison;
- calibration diagnostics;
- verdict;
- safety locks;
- provenance/version.

UNKNOWN must stay UNKNOWN.

## Deliverables

Write continuously to:

- `DESIGN.md`
- `CHECKPOINT.md`

You may add focused evidence/diagram/schema files only inside this directory.

`DESIGN.md` must end implementation-ready and include:
- execution-path map;
- three architecture alternatives;
- selected architecture;
- component/data-flow diagram in Mermaid;
- MCP tool schemas;
- auth/threat model;
- cursor/idempotency/recovery;
- task prompt/run protocol;
- no-lookahead protocol;
- persistence design;
- dashboard API/UI contract;
- experiment/calibration design;
- call/payload budget;
- test strategy;
- staged implementation plan;
- explicit non-goals and rollback.

## Checkpoint discipline

After every completed phase, update `CHECKPOINT.md`.

Before quota exhaustion, stopping or handoff, persist:

```text
NUM_ORDER=ORDER085
STATUS=IN_PROGRESS|HOLD_QUOTA|DESIGN_COMPLETE|BLOCK_REAL
BRANCH=grokbot/order085-gptrader-design
BASE_MAIN_SHA=
CURRENT_BRANCH_SHA=
COMPLETED_PHASES=
CURRENT_PHASE=
FILES_WRITTEN=
KEY_DECISIONS=
MEASURED_FACTS=
UNRESOLVED_QUESTIONS=
NEXT_EXACT_ACTION=
OWNER_DECISIONS_NEEDED=
IMPLEMENTATION_PERFORMED=NO
DEPLOYMENTS=0
D1_READS=0
D1_WRITES=0
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
```

Resume ORDER085 from this checkpoint. Never reset because quota ended.

## Hard rails

- Do not implement GPTrader in ORDER085.
- Do not merge to main.
- Do not interfere with ORDER084.
- Do not read frozen 011 science early.
- Do not deploy.
- Do not mutate Cloudflare/D1.
- Do not enable LIVE.
- Do not add wallet/signing/mainnet order capability.
- Do not tune model weights/thresholds.
- Do not claim EDGE from simulated profit.
- Do not hide negative results.

Final return must include the design path, latest checkpoint SHA, unresolved owner decisions, and recommended implementation sequence.
