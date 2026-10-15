# Binance public-data and REA paper integration — 2026-10-14

## Scope and activation

SENEX now has a locally wired, read-only Binance Spot public-market-data path and an explicitly requested clean-room paper diagnostic. This is a source-tree integration only; no deployment or commit occurred. It does not wire paper_execution_contract.py or its journal/recovery state into the production execution engine.

The local FastAPI router is senecio_polymarket/backend/binance_public_router.py; it is mounted by both senecio_polymarket/backend/main.py (legacy local app) and senecio_polymarket/backend/main_real.py (the container entrypoint in start_single_authority.sh). The existing main_real public-method guard still allows only GET/HEAD/OPTIONS; no mutating route is introduced. Its two GET routes are:

- GET /api/binance/public_klines?symbol=BTCUSDT&interval=4h&limit=24
- GET /api/binance/rea_paper_diagnostic?symbol=BTCUSDT&interval=4h&limit=100

Only BTCUSDT/ETHUSDT and the 15m/1h/4h/1d intervals are permitted by the API/UI route. The limit is 2–100 for the backend dashboard. The route is enabled by default for manual use and can be disabled using SENEX_BINANCE_PUBLIC_DATA_ENABLED=0.

The frontend renders two explicit buttons. Neither route is called on page load and there is no periodic poll, WebSocket, API key, signed request, account query, order/cancel endpoint, or connection to the runtime order executor. The backend validates that the public-data payload declares this safety contract before returning it. HTTP 429/rate-limit failures remain errors; no stale data is silently substituted.

## REA diagnostic

rea_paper_diagnostic converts only fully closed public candles into the existing research/rea_engines Candle contract and runs the clean-room Grid, DCA, and Combo evaluators plus separate buy-and-hold benchmarks. Each request starts a fresh hypothetical ledger. Metrics are aggregate summaries, with 1x/2x/3x cost stress; no individual order tape, live balance, or trade instruction is returned. Defaults are a hypothetical 10,000 USDT, max_inventory=0.02, max_fill_qty=0.02, fee 10 bps, spread 5 bps and slippage 5 bps. These are assumptions, not the user's account fee tier or calibrated execution costs.

The runtime image copies only the pure deterministic evaluator modules (contracts.py, accounting.py, replay.py, strategies.py, evaluation.py) and package markers. paper_execution_contract.py, archived datasets, historical reports and output artifacts are not copied to the runtime image. The five previously protected execution/risk/journal modules were not modified.

## First real public snapshot

A public, unsigned GET request was successfully tested for BTCUSDT 4h, starting at 2026-10-01 00:00 UTC:

- Raw rows returned: 84; fully closed rows used: 83; the open candle was dropped.
- Range: 2026-10-01T00:00:00Z through 2026-10-14T16:00:00Z.
- Detected gaps: 0; no candles were interpolated.
- Raw Klines response SHA-256: ee0f6d1ec6e96a33c9ff85f16e969f6539d75cb42ef4500a6d902c0fee0e37ac.
- Canonical closed-candle SHA-256: e8cd07c2222bbdda06e2a8def1b1dd2b565fdf29a1b88dd038c086c85bdfa71f.
- Local artifacts are under research/rea_engines/runs/binance_public/: normalized CSV, exact raw response JSON, manifest, and paper diagnostic.

That short recent sample produced: Grid 0 fills / 0% return, Combo 0 fills / 0% return, DCA four fills / approximately -0.0177% net return. Fixed-quantity buy-and-hold was approximately -0.4120% over the same sample under the assumed costs. A flat grid/combo result does not mean a strategy is validated; DCA's result is negative and the sample contains only 83 bars. These are diagnostic values, not a robust out-of-sample profitability test and not a trade signal.

## Verification and remaining blockers

Dedicated validations passed before the dashboard diagnostic additions:

- tests/test_rea_binance_spot_public.py: 12 passed, 1 warning.
- tests/test_binance_public_market_data.py: 6 passed, 1 warning.
- tests/test_binance_public_router.py: 7 passed, warnings are upstream deprecation warnings.

The full unfiltered SENEX test suite remains blocked during collection at tests/test_health_readiness.py because the local environment lacks sse_starlette; no dependency was installed. Production/main-app startup or browser rendering has not been verified in this local environment, so the button-to-container behavior must not be described as visually verified. The local API route and components are tested independently.

This integration does not submit real or testnet orders and does not establish profitability. PAPER_ONLY, EDGE=UNPROVEN; live trading and product/runtime order integration remain NOT APPROVED. Before any future money-on deployment: authorize it separately, review official Binance data/API terms for intended use, calibrate fee/filters/precision, add exchange/account reconciliation/authenticated approvals, conduct longer independent OOS and walk-forward validation after fees/slippage, then run a distinct signed-off testnet phase.

## Final verification refresh — 2026-10-14

At local time 2026-10-14T21:06:30.838236-03:00, the post-integration repository suite completed with **685 passed, 92 warnings in 66.13 s**, excluding only tests/test_health_readiness.py. The unfiltered suite remains blocked during collection by missing sse_starlette. Public-data backend, router, diagnostic, integration-surface and REA intake tests passed individually; no dependency installation occurred. The latest post-refresh run completed with 685 passed and 92 warnings in 54.72 seconds. The 121-row source inventory and 300-row manifest are being synchronized and audited after the final status update.

## Extended public sample — 1,000 BTCUSDT 4h candles

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


## Final verification and index audit - 2026-10-14

After refreshing shared metadata, the snapshot exporter exited 0. The 121-row source inventory and 300-row final artifact manifest were cryptographically audited: no missing paths and no size/SHA-256 mismatches. The latest dashboard snapshot suite passed 9 tests with one deprecation warning. Public Binance data/service/router/integration plus paper-execution-contract tests passed 51 tests with two deprecation warnings. The broad applicable Python suite passed 685 tests with 92 warnings, excluding only tests/test_health_readiness.py because the local interpreter does not have sse_starlette; no package was installed.

A local TestClient check on backend/main_real.py returned HTTP 200 for the two public GET endpoints and four dashboard assets, and HTTP 405 for unsafe POST. It used an in-process test import shim for the absent sse_starlette module and does not establish production deployment or browser rendering.
