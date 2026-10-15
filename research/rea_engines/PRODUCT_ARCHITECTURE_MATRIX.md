# Product architecture matrix — evidence separated from inference

Audit date: 2026-10-14. Status labels apply per component. OBSERVED means the evidence itself was inspected; it does not mean private runtime semantics were proven. INFERRED means an independently authored model derived from public concepts. UNKNOWN means evidence is insufficient. BLOCKED means capture stopped at an access challenge or the current scope gate.

For exact byte sizes, timestamps and SHA-256, see SOURCE_FILE_INVENTORY.csv. PRODUCT_ARCHITECTURE_MATRIX.csv carries component-by-component states for all ten requested technical dimensions.

## Gunbot

Observed public surface: custom-strategy context, indicator/config templates, documented Gain/BBTA/SpotGrid rules, published settings and order-method surface. Official package checksum verified. Separately, embedded V8 cached-bytecode artifacts were extracted, parsed, indexed and selected modules translated to pseudocode. gain_ws/bf/rest selected outputs contain 44/47/74 function headers and zero unsupported-opcode warnings. This is not semantic equivalence testing.

Unknown: exact private runtime behavior, precise state transitions, actual order signing/routing, hidden strategy safeguards, exchange precision/retry/partial-fill behavior, crash recovery and profitability.

Blocked: further commercial executable passes pending explicit authorization, asset/license scope and permitted-method review.

## HaasOnline

Observed public surface: public website JavaScript analysis (9/9 scripts, 510,380 AST nodes, 0 parse failures) and official TradeServer MCP documentation. Public docs describe interfaces for bot/script operations and backtests. This is not the HaasScript runtime or trading engine.

Unknown: authenticated execution/risk internals, indicator pipeline, position sizing, fees/fills, persistence/recovery, exchange adapters and commercial strategy performance. No authenticated account/server was used.

## Bitsgap

Observed public surface: public frontend analysis (15/15 scripts, 210,854 AST nodes, 0 parse failures) and public help descriptions of DCA, Grid and Combo. Public descriptions combine grid profit-taking with DCA adverse-side adds on one futures position.

Inferred: lab Grid/DCA/Combo are independently authored spot-only models. They do not implement the proprietary scheduler or Combo's actual futures/margin/liquidation semantics.

Unknown: authenticated execution, strategy state machine, exchange adapter, partial fills, risk caps and product profitability.

## WunderTrading

Blocked: homepage and help capture returned Cloudflare challenge; no challenge solving or bypass was attempted.

Unknown: product frontend beyond blocker response, private strategy engine, order lifecycle, risk, accounting, persistence and profitability.

## Clean-room engines

Observed: independently authored Grid, DCA and Combo with deterministic OHLCV replay, resting limit lifecycle, cash-backed inventory, Decimal accounting, explicit fee/spread/slippage fields, cost stress and canonical report hashing. The 28-test suite passes. BTCUSDT Spot 4h report was reproduced byte-identically.

Not implemented/unknown: realistic queue position, intrabar path, partial-fill probability, venue filters, external/manual fills, rate limits and durable crash recovery. No market-regime labels, second asset/timeframe or prospective forward cohort is present.

## SENEX integration

The existing ExecutionEngine, RiskKernel, TradeJournal, ExecutionSimulator and BinanceSimLane were inspected read-only. Proposed integration is design only: risk veto before intent execution; ExecutionEngine as fill source of truth; idempotent order/fill reconciliation; versioned state; exactly-once journal events; fail-closed recovery. No production source was changed and the research package is not imported into runtime.

## Ten dimensions

The component matrix covers architecture/data flow; market inputs/indicators/normalization; entry/exit; state/position management; sizing/exposure/capital limits; DCA/Grid/trailing/TP/SL/cooldowns; order persistence/cancellation/reconciliation; fees/spread/slippage/partial fills/exchange filters; errors/restart/recovery; and public interfaces/configuration/tests/uncertainty/profitability. Each row cites a local evidence source and hash; the inventory records exact hashes of local evidence artifacts.


## Additional component rows: resilience and ETHUSDT data

The CSV matrix now has 80 dimension-level rows across eight product/component buckets, 10 dimensions per bucket: Gunbot, HaasOnline, Bitsgap, WunderTrading, the clean-room models, SENEX runtime interfaces, SENEX resilience harness and the ETHUSDT second-asset study. Each row carries OBSERVED, INFERRED, UNKNOWN or BLOCKED, specific evidence, a source path/hash and a limitation.

### Resilience harness

The standalone research helper handles explicit intrabar stop/target ambiguity policies, ordered partial fills, exact duplicate event idempotency, duplicate-ID payload conflict detection, checksummed state checkpoint/restart and non-mutating snapshot reconciliation. It is not connected to Replay or SENEX runtime and does not modify production classes.

### ETHUSDT dataset and rights

The independent ETHUSDT Spot 4h CSV contains 14,789 rows from 2020-01 through 2026-09. All 81 monthly archives matched their official CHECKSUM files; a single 8-hour gap in February 2020 is preserved before OOS. CSV SHA-256: d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7. Manifest SHA-256: 2ba32145fcd46b8cc1cd9332b60e65054277f19dbf55249dd9194aa0da55aec4.

The dataset terms allow non-commercial personal non-production historical research and exclude commercial product integration and live proprietary execution absent separate written rights. This data-use restriction is a BLOCKED status for SENEX commercial/product use; the paper model does not connect to runtime. All market-regime labels remain UNKNOWN_NOT_CLASSIFIED and EDGE=UNPROVEN.
