# REA Engine Lab — audit, model evaluation and integration boundary

Date: 2026-10-14. Status: PARTIAL_RESEARCH_COMPLETE. Mode: PAPER_ONLY. EDGE: UNPROVEN. Runtime integration: NOT_APPROVED. Further commercial executable analysis: PAUSED_PENDING_AUTHORIZATION_SCOPE.

## 1. Exact current state

The local SentinelX host is DESKTOP-DPH3941 (win-desktop-dph3941). Existing REA and bytecode-analysis artifacts were inspected in place. No binary was executed and no new extraction/decompilation pass was launched in this audit. The three Gain indexes and selected outputs were verified from disk and hashed. The isolated lab's 28 unit tests passed, compileall passed, and the historical CLI reproduction completed with exit code 0 and byte-identical output to the existing report.

### Gunbot evidence chain

- Vendor ZIP: 285,240,832 bytes, SHA-256 3713922d8af33451ce6c9caacab4d08492fdd8a6d31bd5531b811435aff4d1ea, matching the recorded vendor checksum. The ZIP's gunthy-win.exe entry is 1,027,185,591 bytes; it was not launched.
- EXTRACTION_REPORT.json: status completed; exe_size 1,027,185,591; payload_size 982,148,021; processed 82,051; total_bytes_written 972,660,009; failed 0; errors empty. Manifest: 82,051 rows; all status values ok; 58,831 raw-content, 1,088 v8-bytecode and 22,132 v8-bytecode-alternate rows.
- Embedded V8 caches are extracted artifacts, not original JavaScript source.
- gain_ws parser JSON: 1,188,324,799 bytes. Index: 2,719 SharedFunctionInfo and 2,719 BytecodeArray entries. Selected linear pseudocode: 44 function headers, 1,751,861 bytes, zero unsupported-opcode warnings.
- gain_bf parser JSON: 1,185,964,241 bytes. Index scanned 28,407 objects and has 2,699 SharedFunctionInfo plus 2,699 BytecodeArray entries. Selected linear pseudocode exists: 47 function headers, 1,803,298 bytes, zero unsupported-opcode warnings.
- gain_rest parser JSON: 1,261,378,609 bytes. Index scanned 32,803 objects and has 3,182 SharedFunctionInfo plus 3,182 BytecodeArray entries. Selected linear pseudocode exists: 74 function headers, 2,130,521 bytes, zero unsupported-opcode warnings.
- Zero unsupported-opcode warnings mean the translator emitted a representation for opcodes encountered in these selected outputs. They do not validate reconstructed control flow, register/dataflow interpretation, exception semantics, state transitions, order lifecycle or equivalence with runtime behavior.
- Additional previously generated output families include strategy/runtime, order paths, SpotGrid, asymmetric pyramid, QFL3C, grid levels, stop/target and persistence-related modules. The old core_decompilation_status.json remains a historical run record and contains earlier timeouts/warnings; later translator changes and output hashes postdate it. The audit addendum preserves old records while attaching a current file-based scan.

### Resolve the Gunbot report contradiction

The original REA - Gunbot package report described its then-limited scope: archive integrity, ZIP structure and configuration samples. Its statement that the executable was not decompiled is accurate only for that initial pass, not for the whole later project. Subsequent separate work in GUNBOT-STATIC-EXTRACT extracted embedded payload records and parsed, indexed, disassembled and selectively translated embedded V8 caches to pseudocode. The native executable was not run or patched, and was not decompiled as native machine code. This distinction is recorded in an append-only addendum to the earlier report and README. No old evidence was deleted.

Further passes over the commercial executable are paused until authorization, ownership/license scope and permitted reverse-engineering boundaries are documented. No pseudocode or bytecode-derived implementation is copied into the independent SENEX lab.

## 2. Evidence status vocabulary

OBSERVED means present in a file, public document, hash, test output or inspected API signature. INFERRED means an independent model authored from public evidence and explicit assumptions. UNKNOWN means adequate evidence is absent. BLOCKED means inspection cannot proceed under current constraints.

The component matrix is PRODUCT_COMPONENT_MATRIX.csv. The exact file inventory, timestamps, byte sizes, source descriptions and SHA-256 values are SOURCE_FILE_INVENTORY.csv.

## 3. Product/component summary

Gunbot: vendor package integrity and configuration templates observed; embedded V8 cache extraction/indexing and selected pseudocode observed; semantic reconstruction unknown; further executable analysis blocked pending scope clarification. Public custom-strategy/Gain/BBTA/SpotGrid rules can support independent models, not exact replicas.

HaasOnline: public frontend analysis observed (9/9 scripts, 510,380 AST nodes, 0 parse failures); published TradeServer MCP interface is documented; authenticated engine, private strategy implementation and product profitability unknown.

Bitsgap: public frontend analysis observed (15/15 scripts, 210,854 AST nodes, 0 parse failures); authenticated execution engine, runtime order contracts and profitability unknown.

WunderTrading: homepage/help capture blocked by Cloudflare challenge; no bypass attempted; product engine, private API and profitability unknown.

Clean-room engines: independent Grid, DCA and Combo are implemented and tested. They are public-concept approximations, not exact Gunbot/Bitsgap/HaasOnline/WunderTrading engines. SENEX lab has typed contracts, strategy modules, replay, Decimal accounting, evaluation and CLI. It is not imported by runtime.

## 4. Independent lab contract

The lab reads only a local CSV with required SHA-256. It validates candle fields and chronology, generates strategy intents, replays orders deterministically, accounts cash/inventory and costs, and writes canonical JSON.

- contracts.py: candles, portfolio snapshots, order intents, fills/rejections and cost profile.
- strategies.py: independent spot-only Grid, DCA and Combo.
- replay.py: process orders submitted on earlier candles first; market orders fill at next candle open; resting limits fill only when a later candle range touches them; cancellation, expiry, rejection, pending orders and equity curve.
- accounting.py: Decimal cash/inventory ledger, weighted average cost basis, realized/unrealized PnL and fee/spread/slippage attribution.
- evaluation.py: exact input SHA validation, fixed OOS interval, independent annual slices, benchmarks and cost stress.
- cli.py: local input only; no download fallback, exchange SDK, credentials or order router.

## 5. Dataset and reproducibility

Dataset: Binance Public Data, Spot BTCUSDT 4h, 14,789 rows, 2020-01-01 00:00 UTC through 2026-09-30 20:00 UTC. SHA-256: 7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146, matching dataset-manifest.json. There is one recorded 8-hour gap in February 2020, before OOS; no candle was fabricated.

OOS: 2022-01-01 through 2026-09-30 20:00 UTC, 10,404 evaluated candles. Starting cash 10,000 USDT; max inventory and per-fill cap 0.02 BTC. Costs: fee 10 bps, spread 5 bps, slippage 5 bps; stress 1x/2x/3x. Reproduction CLI exited 0 with empty stderr. Existing and reproduced reports are byte-identical: 11,296,010 bytes, file SHA-256 08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d. Canonical payload hash: 465e48502d88c398ec9f099179d291af240ad26cc14c78ef9a3c37750530bc7b. Report declares historical-paper mode, zero network calls, zero live orders and EDGE UNPROVEN.

| Candidate / benchmark | Net return | Ending equity USDT | Max DD | Fills | Turnover USDT | Time in market | Fees USDT | Spread USDT | Slippage USDT |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Grid clean-room | +0.163% | 10,016.25 | 0.160% | 26 | 2,618.87 | 2.15% | 2.62 | 0.00 | 0.00 |
| DCA clean-room | +1.864% | 10,186.45 | 1.598% | 179 | 17,971.90 | 99.36% | 17.97 | 1.68 | 3.35 |
| Combo clean-room | +0.321% | 10,032.05 | 0.240% | 22 | 2,234.12 | 1.13% | 2.23 | 0.00 | 0.00 |
| Buy-and-hold fixed $100 quote | +0.785% | 10,078.46 | 1.408% | 1 | 100.00 | 99.99% | 0.10 | 0.03 | 0.05 |
| Buy-and-hold fixed 0.02 BTC | +7.346% | 10,734.57 | 11.586% | 1 | 936.26 | 99.99% | 0.94 | 0.23 | 0.47 |

Exact Decimal values remain in the report. Grid/Combo's baseline limit fills record zero spread/slippage by model assumption, not because real limit orders have no execution cost. The $100 quote benchmark is not full-cash exposure. Fixed-quantity buy-and-hold has different exposure and drawdown; it is not a risk-equivalent comparison. Annual slices reset capital and are not compounded.

Cost stress return at 1x/2x/3x: DCA +1.864/+1.687/+1.419%; Grid +0.163/+0.162/+0.137%; Combo +0.321/+0.308/+0.297%. DCA is exposed for approximately 99.36% of OOS bars and retains inventory. These are model outputs, not product returns or proof of robust edge. The separate legacy MA 5/20 paper report returned -5.02% with 12.75% max drawdown; do not merge metrics from different models/reports.

Annual independent slices:
| Model | 2022 return / max DD / fills | 2023 return / max DD / fills | 2024 return / max DD / fills | 2025 return / max DD / fills | 2026 return / max DD / fills |
|---|---|---|---|---|---|
| Grid | +0.163% / 0.160% / 26 | 0.000% / 0.000% / 0 | +0.018% / 0.030% / 4 | +0.089% / 0.122% / 16 | +0.054% / 0.271% / 12 |
| DCA | -1.188% / 1.598% / 28 | +1.316% / 0.331% / 94 | +1.457% / 0.469% / 114 | +0.012% / 0.698% / 45 | -0.039% / 0.859% / 10 |
| Combo | +0.321% / 0.240% / 22 | 0.000% / 0.000% / 0 | +0.029% / 0.030% / 2 | +0.292% / 0.229% / 23 | +0.130% / 0.347% / 9 |

These annual slices independently reset cash and do not represent a compounded portfolio. No market-regime labels, second asset/timeframe or prospective cohort is present.

## 6. Limits preventing an EDGE claim

Only one asset/timeframe; no regime-labeled report; no prospective paper cohort; no queue position, intrabar price ordering, partial-fill likelihood or book depth; no venue filters/precision, minimum notional, rate-limit, retry, clock-skew or external-fill reconciliation model. Public descriptions do not prove private product behavior. EDGE remains UNPROVEN.

## 7. SENEX integration boundary

See research/rea_engines/ARCHITECTURE_DIFFERENCES_SENEX.md. Existing ExecutionEngine, RiskKernel, TradeJournal, ExecutionSimulator and BinanceSimLane were fingerprinted read-only. No file under senecio_polymarket/backend was modified.

Any future adapter must preserve RiskKernel veto/kill-switch authority, ExecutionEngine as fill source of truth, idempotent order/fill reconciliation, explicit state schema/source/config hashes, exactly-once journal events and fail-closed recovery. No runtime import until separate review and approval. No Binance testnet/live access, credentials, deployment, CI activation or trading-lock changes.

## 8. Next task

Keep commercial executable analysis paused. Review the matrix and scope reconciliation. Then add independent tests for intrabar ambiguity, partial-fill/restart state and idempotent reconciliation; acquire another zero-cost authorized dataset if available, otherwise mark cross-asset validation blocked. Keep EDGE UNPROVEN until a preregistered walk-forward and prospective paper evaluation support a stronger conclusion.


## 9. Annual, cost-stress and exposure supplements

- runs/annual-metrics-20261014.csv contains annual independent-slice results for Grid, DCA, Combo and both benchmarks. Each row explicitly labels the market regime UNKNOWN_NOT_CLASSIFIED because no validated regime labels are in the dataset.
- runs/cost-stress-summary-20261014.csv records 1x/2x/3x costs for each candidate and benchmark.
- runs/exposure-and-regime-supplement-20261014.json records derived average and maximum notional exposure from each base OOS equity curve, defined per candle as inventory quantity times close divided by equity. These are derived diagnostics, not native report fields.
- Annual exposure by reset slice is not claimed from the full OOS path. Annual return/drawdown/fill/cost metrics come from independently reset annual runs; overall exposure comes from the base full OOS curve.


## 10. Resilience mechanics and second asset

research/rea_engines/execution_resilience.py is a separate paper-only module, not called by Replay.run and not imported by production runtime. It implements:
- Explicit long-bracket OHLC collision policy: worst-case stop-first, best-case target-first sensitivity, or reject the ambiguous candle.
- Sequenced partial-fill accounting with Decimal costs, remaining quantity, inventory cost basis, realized PnL and state transitions.
- Fill-ID and client-order idempotency. Exact replay of an identical fill is a no-op; reusing a fill ID with a different payload fails closed.
- Local atomic checkpoint persistence with canonical JSON SHA-256, schema guard, source/config hash validation and tamper detection.
- Supplied snapshot reconciliation for cash, inventory, event sequence and open-order remainder. It reports discrepancies and never silently overwrites local state.

The dedicated test file contains 19 focused cases. The helper does not route orders, query accounts or call network services. It does not alter existing BTC replay output.

### Second asset / licence boundary

A second local dataset was downloaded: Binance Public Data Spot ETHUSDT 4h, January 2020 to September 2026, 14,789 data rows. All 81 monthly ZIPs matched the accompanying official CHECKSUM; canonical CSV SHA-256 is d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7, manifest SHA-256 is 2ba32145fcd46b8cc1cd9332b60e65054277f19dbf55249dd9194aa0da55aec4. One 8-hour historical gap in February 2020 is retained before OOS. The verification utility is local-only and makes no network calls.

A pinned run is at research/rea_engines/runs/ethusdt-4h-second-asset-evaluation-20261014.json. It evaluates 10,404 OOS bars from 2022-01-01 to 2026-09-30, with all grid bounds derived from 2020-2021 training data only. tools/evaluate_ethusdt_second_asset.py reproduces this report byte-for-byte.

Legal gate: data/binance/DATA_USE_NOTICE.md records the current Binance Vision Dataset Terms and source URLs. The free download terms allow non-commercial personal non-production historical research only; commercial product integration and live/proprietary execution remain blocked without additional written rights. The second-asset backtest is evidence that the code can be tested on another series, not approval for product deployment and not evidence of edge.

## 11. Test and reproducibility state after this audit

- pytest -q tests/test_rea_engine_resilience.py: 19 passed, 1 warning, exit 0.
- All five REA engine test files under pytest: 47 passed, 1 warning, exit 0.
- Full repository pytest collection: exit 2 before executing tests because sse_starlette is absent while collecting tests/test_health_readiness.py; no package was installed.
- Repository suite excluding only that blocked file: 619 passed, 92 warnings, exit 0. This is not the full suite because the skipped file could not be imported.
- compileall over the REA package and five REA test files: exit 0.
- Offline ETH dataset verifier: exit 0, 81 archives checked, no checksum mismatches.
- ETH evaluation reproduction: exit 0; original and reproduction outputs are byte-identical.
- BTC evaluation reproduction: exit 0; original and final reproduction outputs are byte-identical (11,296,010 bytes, SHA-256 08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d).
- Pytest was discovered in an existing base Python installation (pytest 8.4.2) and used directly; the SentinelX venv does not include pytest. No dependencies were installed.
- The full historical V8 assembler suite remains historically non-green (241 tests, 1 failure, 5 errors) and was not rerun because commercial executable work is paused.

All market-regime labels remain UNKNOWN_NOT_CLASSIFIED. Both BTC and ETH results remain historical paper experiments. EDGE=UNPROVEN.

## Continuation audit — 2026-10-14 (read-only)

### Baseline comparison of the five SENEX interfaces

The current bytes and SHA-256 values match both the recorded SOURCE_FILE_INVENTORY.csv and FINAL_ARTIFACT_SHA256_MANIFEST_2026-10-14.csv entries. This verifies agreement with the recorded baseline; it is not a claim that every duplicate copy elsewhere on disk is identical.

| Relative path | Bytes | SHA-256 | Result |
|---|---:|---|---|
| senecio_polymarket/backend/portfolio/execution_engine.py | 36157 | 3ad44c2474047aac110e71a9924276ac7cb391a83e565454b885754bf50d7523 | MATCH |
| senecio_polymarket/backend/portfolio/risk_kernel.py | 18810 | 9e8291655795051a1296db7475044bda67775d974cee2c8aad24ebcf80cf49c5 | MATCH |
| senecio_polymarket/backend/portfolio/trade_journal.py | 14837 | 12f81c94b8db44a8a247b308d8de910ca61771720240ad2c7daf31bfd10041f2 | MATCH |
| senecio_polymarket/backend/execution_simulator.py | 16671 | 3381a6ecfa888ddde6c56e5fcee2b228e22fb6225704e060da84fd3440192291 | MATCH |
| senecio_polymarket/backend/binance_sim_lane.py | 16461 | 4a799b61fbf00868e7636a204ccdabda0868a391b871e6b8620f67af0545aa0f | MATCH |

No production interface was edited.

### Concrete execution and accounting differences

1. **Order lifecycle:** Replay.run generates strategy intents from candles, maintains pending orders, applies TTL expiry/cancellation, and fills a whole pending quantity on a later candle. execution_resilience.apply_fill consumes externally supplied fill events and tracks cumulative partial quantity and remaining quantity. The replay loop has no partial-fill event model; the resilience helper has no candle loop, strategy-intent handling, TTL or cancellation lifecycle.
2. **Execution-price model:** replay computes fills from later OHLC candles (market at next open; limit when a later range touches the limit). Its baseline model charges modeled spread/slippage only for market orders and records zero spread/slippage for limit fills. Resilience accepts a fill price and costs supplied by the caller; it does not infer queue position, fill probability, market impact or costs from candles.
3. **Intrabar brackets:** resolve_long_bracket has an explicit stop-first / target-first-sensitivity / reject-ambiguous policy. Replay.run does not call this resolver or simulate a bracket stop/target lifecycle.
4. **Accounting arithmetic:** both paths debit buys by notional plus fee/spread/slippage and credit sells by notional minus those costs, allocating remaining basis pro rata and recording realized net PnL. The difference is cost sourcing: replay derives costs from CostProfile; resilience trusts event fields. No arithmetic mismatch was established for equivalent inputs in this read-only pass.
5. **Risk and order guards:** replay's Ledger is constructed with max_inventory and max_fill_qty limits. The standalone resilience state enforces cash availability, no short inventory, order remainder/overfill and event idempotency, but does not carry those two replay caps. It must not be treated as a replacement for the risk kernel.
6. **Recovery/reconciliation:** resilience persists and validates a local checkpoint and compares a supplied snapshot. Replay starts a fresh ledger per run and does not resume from that checkpoint. Snapshot reconciliation is not wired into replay or any external execution source.

These are integration-contract differences, not evidence of an observed production regression. The clean-room helper remains standalone and paper-only. No new test was added merely to encode the absence of integration; the existing tests exercise the helper's current contract. A future adapter needs an approved shared state/event contract and cross-module parity tests before any integration proposal.

### Fresh verification in this continuation

- python -m pytest -q tests/test_rea_engine_resilience.py: **19 passed, 1 warning** (exit 0).
- python -m pytest -q tests/test_rea_engine_accounting.py tests/test_rea_engine_evaluation.py tests/test_rea_engine_replay.py tests/test_rea_engine_strategies.py tests/test_rea_engine_resilience.py: **47 passed, 1 warning** (exit 0).
- python -m pytest -q --ignore=tests/test_health_readiness.py: **619 passed, 92 warnings** (exit 0).
- Unfiltered python -m pytest -q: **exit 2 during collection** because sse_starlette is absent while importing tests/test_health_readiness.py. No dependency was installed.
- The resilience source and its test file retained their prior SHA-256 values; no executable or runtime code was changed.

### Decision

PARTIAL_RESEARCH_COMPLETE; PAPER_ONLY; EDGE=UNPROVEN; product/runtime integration NOT APPROVED. Commercial executable analysis remains paused pending explicit authorization and scope. No account access, credentials, network trading, testnet/live orders, deployment, or runtime wiring occurred.



## Continuation audit - 2026-10-14 (cross-module parity)

- Added `tests/test_rea_engine_accounting_resilience_parity.py`, an offline regression suite comparing the independent `Ledger` and `PaperState` reducers on identical partial buy/sell fill events. It asserts cash, inventory, basis, realized PnL, fee/spread/slippage totals and turnover after each fill, and confirms an exact duplicate fill causes no state drift. Focused result: 2 passed, 1 existing pytest-asyncio deprecation warning.
- The six REA test files now pass: 49 passed, 1 warning, exit 0.
- Repository suite excluding only `tests/test_health_readiness.py`: 621 passed, 92 warnings, exit 0. The unfiltered repository suite remains blocked during collection because `sse_starlette` is absent when importing `tests/test_health_readiness.py`. No dependency was installed; this is not reported as a full-suite pass.
- Added `research/rea_engines/SHARED_EVENT_STATE_CONTRACT_PROPOSAL_2026-10-14.md`. It is a design proposal only, not an approved integration contract. It explicitly preserves the current PaperState v1 fill-sequence semantics, defines candidate event/state invariants, records missing adapter gates, and lists parity/recovery/reconciliation tests.
- No runtime Python module, production interface, order router, risk control, credentials or executable was changed. Replay and Resilience remain decoupled; no new network activity or deployment occurred.

Decision remains `PARTIAL_RESEARCH_COMPLETE`; `PAPER_ONLY`; `EDGE=UNPROVEN`; product/runtime integration NOT APPROVED. Commercial executable analysis remains paused pending explicit authorization and scope. The parity test demonstrates equivalent accounting only for identical synthetic inputs; it does not establish live execution equivalence, profitability or production readiness.


## Continuation audit - 2026-10-14 (contract closure)

- Implemented the isolated paper-only wrapper `research/rea_engines/paper_execution_contract.py` and 22 regression tests in `tests/test_paper_execution_contract.py`. Covered cancel/expiry terminal semantics, idempotent retries, hash-chained append-only JSONL, fsync-before-in-memory-commit, partial-write failure, stale writer rejection, checkpoint prefix verification, replay of later events, risk rejection without journal/state mutation, and reconciliation freeze/resolution.
- Focused wrapper suite: 22 passed, 1 existing pytest-asyncio deprecation warning. All REA tests (accounting, evaluation, replay, strategies, resilience, parity, and paper execution contract): 71 passed, 1 warning. Eight-file REA/dashboard bundle: 80 passed, 1 warning. Targeted `compileall`: exit 0. Four frontend JavaScript files also pass `node --check`.
- Repository suite `python -m pytest -q --ignore=tests/test_health_readiness.py`: 652 passed, 92 warnings, exit 0. Unfiltered collection: 652 tests discovered, but collection stops with one error importing `tests/test_health_readiness.py` because `sse_starlette` is not installed. No package was installed; the unfiltered suite is not claimed as passing.
- Rechecked the five SENEX interfaces and the pre-existing `execution_resilience.py` / `test_rea_engine_resilience.py`; all seven SHA-256 values remain identical to the previously recorded baseline. No production/runtime module was changed or wired to the wrapper.
- Updated the shared contract status, verification matrix, reports, source inventory and final artifact manifest. All manifest and inventory paths, file sizes and SHA-256 digests are checked after update.

Decision remains `PARTIAL_RESEARCH_COMPLETE`; `PAPER_ONLY`; `EDGE=UNPROVEN`; product/runtime integration **NOT APPROVED**. Journal/writer guarantees are local file-harness behavior, not production-journal proof. Authenticated approval, production `TradeJournal` authority, venue filters/precision/min-notional, external retry/clock-drift, and separate security/architecture review remain integration blockers.

## Continuation audit — causal market-regime labels (2026-10-14)

The earlier UNKNOWN_NOT_CLASSIFIED status is superseded for descriptive 4-hour BTCUSDT/ETHUSDT research labels. Added tools/classify_market_regimes.py and tests/test_rea_market_regimes.py. The classifier uses a 180-bar trailing window, +/-10% 30-day trend thresholds, and a per-asset volatility threshold estimated from valid windows ending by 2021-12-31T23:59:59Z. It labels only OOS rows from 2022-01-01 through 2026-09-30, holds the training percentile fixed, and omits windows crossing timestamp gaps rather than fabricating bars.

Outputs:
- runs/causal-regimes-BTCUSDT-4h-20261014.csv — 10,404 rows, SHA-256 ef84e553b3c73aa7c2ccebb1d06d8a1bdd03b5371a1b7ded58df8557e8f76f70.
- runs/causal-regimes-ETHUSDT-4h-20261014.csv — 10,404 rows, SHA-256 274e043654b8b7286d9d06255097cfe06e62ef7d77a99060cb39aca1c5754c46.
- runs/market-regime-counts-cross-asset-20261014.csv — annual-by-regime counts.
- Methodology: research/rea_engines/MARKET_REGIME_CLASSIFICATION_2026-10-14.md.

Focused tests: 4 passed, 1 existing pytest-asyncio deprecation warning. Labels are descriptive covariates only. They were not used to tune strategies, do not validate future returns, and do not alter EDGE=UNPROVEN. ETH reuse remains subject to the data-use notice and its non-commercial/non-production boundary.
