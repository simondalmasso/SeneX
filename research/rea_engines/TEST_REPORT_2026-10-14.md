# Test, dataset integrity and reproduction report — 2026-10-14

## Interpreter discovery

Command: C:/ProgramData/SentinelX/.venv/Scripts/python.exe -m pytest --version
Exit 1 — No module named pytest. No installation was attempted.

Existing local base interpreter command: C:/Users/Simon/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest --version
Exit 0 — pytest 8.4.2. Used directly without modifying either environment.

## Targeted tests

1. C:/Users/Simon/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest -q tests/test_rea_engine_resilience.py
   Exit 0 — 19 passed, 1 DeprecationWarning, 0.12 s. Covers intrabar ambiguity and gaps, partial buy/sell fills, overfill rejection, cash exhaustion, restart/checksum, source/config mismatch, reconciliation, event gaps, exact duplicate idempotency and duplicate-ID/different-payload conflict.

2. C:/Users/Simon/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest -q tests/test_rea_engine_accounting.py tests/test_rea_engine_evaluation.py tests/test_rea_engine_replay.py tests/test_rea_engine_strategies.py tests/test_rea_engine_resilience.py
   Exit 0 — 47 passed, 1 DeprecationWarning, 0.33 s.

3. C:/ProgramData/SentinelX/.venv/Scripts/python.exe -m compileall -q research/rea_engines tests/test_rea_engine_accounting.py tests/test_rea_engine_evaluation.py tests/test_rea_engine_replay.py tests/test_rea_engine_strategies.py tests/test_rea_engine_resilience.py
   Exit 0 — no output and no syntax errors.

## Full repository suite

4. C:/Users/Simon/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest -q
   Exit 2 — collection stopped at tests/test_health_readiness.py because importing senecio_polymarket/backend/ws_server.py raises ModuleNotFoundError: No module named 'sse_starlette'. One Starlette deprecation warning printed. Collection interruption means this was not a completed full-suite run. No dependency installed.

5. C:/Users/Simon/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest -q --ignore=tests/test_health_readiness.py
   Exit 0 — 619 passed, 92 warnings, 36.34 s. This covers the tree except the single file blocked above; it is not described as a fully green unfiltered suite.

## Dataset verification

6. C:/ProgramData/SentinelX/.venv/Scripts/python.exe research/rea_engines/tools/verify_public_dataset.py --csv research/rea_engines/data/binance/ETHUSDT-4h-2020-01_to_2026-09.csv --manifest research/rea_engines/data/binance/ETHUSDT-4h-dataset-manifest.json --expected-csv-sha256 d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7
   Exit 0 — status=verified, 14,789 rows, exact CSV SHA matched, 81 monthly archives and checksums verified, zero errors. One 8-hour gap in February 2020 is recorded before OOS. Verifier performs no network request.

## Historical evaluation reproduction

7. C:/ProgramData/SentinelX/.venv/Scripts/python.exe -m research.rea_engines.cli --input "C:/ProgramData/SentinelX/workspace/trading-bot-rea-20261013/INDEPENDENT-PAPER-BOT/data/binance/BTCUSDT-4h-2020-01_to_2026-09.csv" --expected-sha256 7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146 --output research/rea_engines/runs/audit-reproduction-final-20261014.json
   Exit 0 — 14,789 source rows / 10,404 OOS rows, EDGE=UNPROVEN; byte-identical to original report, 11,296,010 bytes, SHA-256 08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d. Canonical payload SHA-256 465e48502d88c398ec9f099179d291af240ad26cc14c78ef9a3c37750530bc7b.

8. C:/ProgramData/SentinelX/.venv/Scripts/python.exe research/rea_engines/tools/evaluate_ethusdt_second_asset.py --output research/rea_engines/runs/ethusdt-4h-second-asset-reproduction-check-20261014.json --overwrite
   Exit 0 — reproduction exactly matches runs/ethusdt-4h-second-asset-evaluation-20261014.json byte-for-byte; 11,643,540 bytes, SHA-256 052c64407ff43c4bd452c73e1d3acc600af083c50c0f911539102402cb544833. Canonical payload SHA-256 3099992679905831a18fb27d2edc437212c5e48a6cb39dddd542bc9cb0525e19. Overwritten target is only the disposable reproduction-check report, not the canonical report.

## TDD record

An early behavior-oriented run of AdditionalResilienceContractTests returned nonzero because a reused fill ID with changed payload was accepted and exact cash exhaustion was rejected. The implementation was amended with full fill fingerprints and non-negative (rather than positive) cash state. The focused suite subsequently passed 19/19. Those tests remain in the suite.

## Remaining limits

- OHLCV cannot prove queue position, intrabar price order, partial-fill probability or exchange-book depth.
- Resilience helper is standalone and is not wired to the replay loop or production runtime.
- tests/test_health_readiness.py remains blocked from collection because sse_starlette is missing; no package installed.
- No second-asset market-regime labels exist; all regime labels remain UNKNOWN_NOT_CLASSIFIED.
- Binance Vision terms restrict ETH data use to non-commercial personal non-production research; commercial product/live use needs separate written rights.
- Historical full V8 assembler suite remains non-green (241 tests, 1 failure, 5 errors), not rerun here. Commercial executable analysis remains paused.
- EDGE=UNPROVEN; no live/testnet calls, account access, orders, deploy or runtime integration.

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

## Continuation audit — 2026-10-14 (durable paper execution contract)

Recorded at local time 2026-10-14T20:39:04.499612-03:00 after the SentinelX host reconnected and after the final snapshot/index validation.

- Verified SHA-256 for research/rea_engines/paper_execution_contract.py: 3f6eb48a93a7d6e00ff8cf4c1d79ac6e621d745fa85d261c0bc45445235e301b.
- Verified SHA-256 for tests/test_paper_execution_contract.py: cb7f07d39f27949cde7d29839004e92716b4e33043da18d142975befcf928dfb.
- pytest -q tests/test_paper_execution_contract.py: **22 passed, 1 deprecation warning**.
- Final eight-file focused regression command covering REA, the durable contract and dashboard snapshot: **80 passed, 1 warning in 16.30s**.
- pytest -q --ignore=tests/test_health_readiness.py: **652 passed, 92 warnings in 68.49s**.
- The unfiltered pytest -q remains blocked during collection at tests/test_health_readiness.py by ModuleNotFoundError: sse_starlette; no package installation was attempted.
- rea_research_snapshot.js and rea_research_panel.js both pass node --check.
- All five protected runtime SHA-256 values match the previously recorded values.

This is a current continuation result, superseding earlier 619/621 repository-suite counts for the tested tree. It does not turn the excluded health-readiness test into a pass and does not establish profitability. The paper wrapper remains isolated and not runtime-wired; EDGE=UNPROVEN.



## Continuation audit - 2026-10-14 (contract closure)

- Implemented the isolated paper-only wrapper `research/rea_engines/paper_execution_contract.py` and 22 regression tests in `tests/test_paper_execution_contract.py`. Covered cancel/expiry terminal semantics, idempotent retries, hash-chained append-only JSONL, fsync-before-in-memory-commit, partial-write failure, stale writer rejection, checkpoint prefix verification, replay of later events, risk rejection without journal/state mutation, and reconciliation freeze/resolution.
- Focused wrapper suite: 22 passed, 1 existing pytest-asyncio deprecation warning. All REA tests (accounting, evaluation, replay, strategies, resilience, parity, and paper execution contract): 71 passed, 1 warning. Eight-file REA/dashboard bundle: 80 passed, 1 warning. Targeted `compileall`: exit 0. Four frontend JavaScript files also pass `node --check`.
- Repository suite `python -m pytest -q --ignore=tests/test_health_readiness.py`: 652 passed, 92 warnings, exit 0. Unfiltered collection: 652 tests discovered, but collection stops with one error importing `tests/test_health_readiness.py` because `sse_starlette` is not installed. No package was installed; the unfiltered suite is not claimed as passing.
- Rechecked the five SENEX interfaces and the pre-existing `execution_resilience.py` / `test_rea_engine_resilience.py`; all seven SHA-256 values remain identical to the previously recorded baseline. No production/runtime module was changed or wired to the wrapper.
- Updated the shared contract status, verification matrix, reports, source inventory and final artifact manifest. All manifest and inventory paths, file sizes and SHA-256 digests are checked after update.

Decision remains `PARTIAL_RESEARCH_COMPLETE`; `PAPER_ONLY`; `EDGE=UNPROVEN`; product/runtime integration **NOT APPROVED**. Journal/writer guarantees are local file-harness behavior, not production-journal proof. Authenticated approval, production `TradeJournal` authority, venue filters/precision/min-notional, external retry/clock-drift, and separate security/architecture review remain integration blockers.

An intermediate repeat of the broad suite timed out at the wrapper's 58-second limit without a pytest result; the subsequent extended run completed with 652 passed and exit code 0. The timeout is recorded separately in FINAL_TEST_COMMANDS_2026-10-14.json.

## Continuation audit — causal market-regime labels (2026-10-14)

The earlier UNKNOWN_NOT_CLASSIFIED status is superseded for descriptive 4-hour BTCUSDT/ETHUSDT research labels. Added tools/classify_market_regimes.py and tests/test_rea_market_regimes.py. The classifier uses a 180-bar trailing window, +/-10% 30-day trend thresholds, and a per-asset volatility threshold estimated from valid windows ending by 2021-12-31T23:59:59Z. It labels only OOS rows from 2022-01-01 through 2026-09-30, holds the training percentile fixed, and omits windows crossing timestamp gaps rather than fabricating bars.

Outputs:
- runs/causal-regimes-BTCUSDT-4h-20261014.csv — 10,404 rows, SHA-256 ef84e553b3c73aa7c2ccebb1d06d8a1bdd03b5371a1b7ded58df8557e8f76f70.
- runs/causal-regimes-ETHUSDT-4h-20261014.csv — 10,404 rows, SHA-256 274e043654b8b7286d9d06255097cfe06e62ef7d77a99060cb39aca1c5754c46.
- runs/market-regime-counts-cross-asset-20261014.csv — annual-by-regime counts.
- Methodology: research/rea_engines/MARKET_REGIME_CLASSIFICATION_2026-10-14.md.

Focused tests: 4 passed, 1 existing pytest-asyncio deprecation warning. Labels are descriptive covariates only. They were not used to tune strategies, do not validate future returns, and do not alter EDGE=UNPROVEN. ETH reuse remains subject to the data-use notice and its non-commercial/non-production boundary.

## Continuation audit — Binance public REA data integration

Recorded locally at 2026-10-14T21:00:56.253909-03:00. The local source tree now includes a manually invoked, read-only Binance Spot public-data route and a separate aggregate paper-diagnostic route. No deployment occurred.

- GET /api/binance/public_klines returns fully closed public candles, source/canonical SHA-256 provenance, gap warnings, a 60-second memory cache and rate-limited cold pulls.
- GET /api/binance/rea_paper_diagnostic runs the isolated Grid/DCA/Combo evaluators and buy-and-hold benchmarks on the same recent sample, with explicit 1x/2x/3x cost stress. It returns aggregate metrics only and never routes a signal to ExecutionEngine.
- Focused test results: public-data service 6 passed; router including diagnostic 7 passed; integration-surface guards 4 passed; independent REA Binance intake 12 passed. The edited Python surfaces compile, and four dashboard JavaScript files pass Node syntax checks.
- A real unauthenticated request produced 83 closed BTCUSDT 4h candles from 2026-10-01 00:00 UTC to 2026-10-14 16:00 UTC; one forming candle was dropped and no gaps were found. This recent sample showed Grid/Combo with zero fills and DCA approximately -0.0177% net under assumptions; it does not demonstrate profitability.
- The unfiltered full repository test remains blocked at collection by missing sse_starlette; no dependency was installed. Production startup and browser rendering are not claimed as verified.

The runtime image only includes the pure deterministic REA contracts/accounting/replay/strategies/evaluation modules needed by the diagnostic. paper_execution_contract.py remains isolated and is not packaged or imported by this endpoint. ExecutionEngine, RiskKernel and TradeJournal do not receive orders or data mutations. PAPER_ONLY; EDGE=UNPROVEN; no deployment; runtime live integration is NOT APPROVED.

## Final post-integration verification — 2026-10-14

Recorded locally at 2026-10-14T21:06:30.838236-03:00.

- Full applicable repository suite after adding the Binance public-data integration: **685 passed, 92 warnings, exit 0 in 66.13 s**, excluding only tests/test_health_readiness.py.
- Focused REA/contract/dashboard bundle: 80 passed, 1 warning in the last verified run.
- Dedicated tests added in this continuation: public-data adapter 6 passed; public router and REA paper diagnostic 7 passed; static integration surface 4 passed; clean-room Binance intake 12 passed.
- Real public API verification: backend service returned 23 closed BTCUSDT 4h candles from a limit-24 request, dropped one still-open candle, detected zero gaps, and reported no key, signed request, account endpoint or order endpoint. A separate saved research sample contains 83 closed bars from October 1 to October 14.
- The unfiltered suite remains NOT VERIFIED because collection fails at tests/test_health_readiness.py due to missing sse_starlette. No dependency was installed.
- Python compile and four Node syntax checks passed. The local FastAPI router was tested in isolation; full main-app startup/browser rendering was not verified, and no deployment occurred.

No actual/testnet orders or API keys. The recent short sample is not proof of an edge; DCA was approximately -0.0177% net under assumed costs while Grid/Combo had no fills. PAPER_ONLY; EDGE=UNPROVEN.

## Binance public REA sample — 1,000 closed BTCUSDT 4h candles

A 1,000-closed-bar public-data snapshot was fetched on 2026-10-14 using Binance Spot public GET endpoints only. Window: 2026-05-01 00:00 UTC through 2026-10-14 12:00 UTC; zero gaps, no interpolated candles, no incomplete candle in the returned 1,000 rows.

- Raw Klines response SHA-256: 1a6b578307b867cf2816990276cf876218c1914ee75a0c993a823548e84bf56b.
- Canonical closed-candle SHA-256: 23b2e9d9cdf5b903dfd48c34a1cc7c62f4ebabb00e34c16193eb9ef748ffabc3.
- Grid: +0.0090% net / +0.90 USDT; 2 fills; max drawdown about 0.168%; 3x cost stress about +0.0070%.
- Combo: +0.0575% net / +5.75 USDT; 4 fills; max drawdown about 0.266%; 3x cost stress about +0.0535%.
- DCA: +0.1918% net / +19.18 USDT; 17 fills; max drawdown about 0.553%; market exposure about 99.3%; 3x cost stress about +0.1478%.
- Fixed-quantity buy-and-hold benchmark: +0.8751% / +87.51 USDT; max drawdown about 4.78%. Quote-allocation buy-and-hold: about +0.0567% / +5.67 USDT.

These are fixed-parameter clean-room simulations on one recent market window with hypothetical 10,000 USDT, inventory/fill caps of 0.02, and assumed costs of fee 10 bps, spread 5 bps and slippage 5 bps. DCA was nearly fully exposed and materially lagged fixed-quantity buy-and-hold; Grid and Combo traded very little. This does not establish a durable edge or a trade recommendation. EDGE remains UNPROVEN, and trading is not enabled.

## Binance public-data REA continuation — 2026-10-14

A separate clean-room paper replay used 1000 closed BTCUSDT 4-hour candles from 2026-05-01T00:00:00.000000Z through 2026-10-14T12:00:00.000000Z. Detected gaps: 0; missing candles were not interpolated. The raw public response SHA-256 is 1a6b578307b867cf2816990276cf876218c1914ee75a0c993a823548e84bf56b and canonical candles SHA-256 is 23b2e9d9cdf5b903dfd48c34a1cc7c62f4ebabb00e34c16193eb9ef748ffabc3.

Hypothetical starting cash was 10,000 USDT; max fill and inventory were each capped at 0.02 BTC; assumed costs were 10 bps fee, 5 bps spread and 5 bps slippage. Aggregate results:

- GRID: +0.90 USDT (+0.0090%), 2 fills, max drawdown 0.1677%; 3x costs +0.70 USDT (+0.0070%).
- COMBO: +5.75 USDT (+0.0575%), 4 fills, max drawdown 0.2663%; 3x costs +5.35 USDT (+0.0535%).
- DCA: +19.18 USDT (+0.1918%), 17 fills, max drawdown 0.5533%; 3x costs +14.78 USDT (+0.1478%).
- Benchmark Fixed-quantity buy-and-hold: +87.51 USDT (+0.8751%), max drawdown 4.7779%.
- Benchmark 100-USDT quote allocation buy-and-hold: +5.67 USDT (+0.0567%), max drawdown 0.3128%.

These are fixed-parameter simulations over one recent window, not an out-of-sample proof. DCA was active during about 99.3% of bars, but that does not mean 99.3% capital exposure. The nominal PnL comparison is not exposure-normalized: fixed-quantity buy-and-hold holds 0.02 BTC, whereas DCA ends with about 0.001178 BTC. Grid and Combo traded very little. Compare equal-notional and equal-risk-budget variants before interpreting relative performance. The data does not establish a durable edge or expected future profit. State remains EDGE=UNPROVEN.

The read-only Binance router and REA diagnostic GET routes are now mounted in local source for both backend.main:app and the container entrypoint backend.main_real:app. Local TestClient checks returned HTTP 200 for public klines and paper diagnostics, served the dashboard/static assets, and rejected POST with 405 DENY_UNSAFE_METHOD. No API key, signed request, account endpoint or order endpoint was used. This source has not been deployed; it is not connected to the production trade engine and no live/testnet orders were submitted.

The local test interpreter lacks sse_starlette even though requirements.lock pins it. The main_real route smoke test used a test-only import shim for that dependency; no package was installed. The full unfiltered pytest run remains blocked at collection on tests/test_health_readiness.py. The applicable suite excluding only that file passed 685 tests with 92 warnings.


## Final verification after index refresh ? 14 October 2026

- Broad applicable Python suite: 685 passed, 0 failed, 92 warnings, exit 0 in 54.72 seconds. Command excludes only tests/test_health_readiness.py because its import fails on missing sse_starlette.
- Dashboard snapshot plus market-regime classifier: 13 passed, 1 existing pytest-asyncio deprecation warning.
- Independent paper bot: 32 passed; clean-room engine family: 31 passed.
- Four-file public Binance route/data suite: 29 passed, 2 deprecation warnings.
- 1,000-bar BTCUSDT 4h sample: 1,000 closed bars, zero gaps, no forming candle in the returned rows; checksummed raw response, CSV and paper result recorded in the status JSON and integration specification.
- Source inventory and final artifact manifest were refreshed after concurrent-file changes. The final cryptographic audit result will be recorded after the last manifest check.
- The failed intermediate 676-pass/9-snapshot-error run was a stale-index failure; the index was corrected, focused dashboard/regime tests passed, and the full applicable rerun completed successfully. The unfiltered suite remains blocked by the missing optional dependency.

State: PARTIAL_RESEARCH_COMPLETE; PAPER_ONLY; EDGE=UNPROVEN; runtime order integration NOT APPROVED.


## Final index and test closure - 14 October 2026

Current verification after synchronization: source inventory 121 rows and final artifact manifest 300 rows, each with zero missing paths and zero size/SHA-256 mismatches. The dashboard snapshot exporter exited 0. The broad applicable Python suite passed 685 tests with 92 warnings in 54.72 seconds, excluding only tests/test_health_readiness.py because the environment lacks sse_starlette. Dashboard snapshot tests passed 9/9; the public Binance integration/router/service/intake plus durable paper-execution-contract bundle passed 51/51 with 2 deprecation warnings. Separate suites: independent paper bot 32/32, clean-room strategy families 31/31, market regime classifier 4/4.

A local main_real TestClient run, logged in FINAL_TEST_COMMANDS_2026-10-14.json, returned HTTP 200 for public GET endpoints and dashboard assets and denied unsafe POST with HTTP 405. It used a process-local import shim for missing sse_starlette, not an installed dependency; this does not claim a deployed browser session or production service.

The 1,000-bar BTCUSDT 4-hour sample remains descriptive: DCA +0.1918%, Grid +0.0090%, Combo +0.0575%, quote-allocation buy-and-hold +0.0567%, and fixed-quantity buy-and-hold +0.8751%, under stated hypothetical costs. No robust edge is established.

## Full collection verification via temporary test-only import shim — 14 October 2026

A fresh full repository run was executed from the repository root with python -m pytest -q --disable-warnings, using the installed local Python 3.14 interpreter and a temporary sitecustomize.py outside the repository. The shim only satisfied the missing sse_starlette.sse.EventSourceResponse import by mapping it to Starlette StreamingResponse, so the suite could collect.

Result: **696 passed, 93 warnings, exit 0 in 48.43 seconds**.

Scope caveat: this validates the collected tests under the temporary import shim. It does not verify actual SSE streaming semantics, a production dependency install, container build/startup, browser rendering, deployed runtime, or exchange execution. The shim was removed after the run and is not part of the source tree. The ordinary unshimmed full collection still requires the pinned sse_starlette dependency. No dependency installation, network trading call, credential use, order submission, or deployment occurred.

## Publication boundary

The Git-tracked package includes source, tests, architecture/methodology documentation, and summarized verification. Locally generated backtest JSON/CSV outputs, Python bytecode, backup files, and raw ETHUSDT candles/source archives are intentionally excluded from the public repository update. Their local hashes/evidence remain in the workstation artifact record. ETH reuse remains subject to data/binance/DATA_USE_NOTICE.md; no commercial or production rights are implied.