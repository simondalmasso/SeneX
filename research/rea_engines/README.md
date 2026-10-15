# SENEX REA Engine Lab

This package is an **offline, historical-paper research lab**. It does not connect to an exchange and must not be imported into SENEX runtime code. Grid, DCA, and Combo are clean-room approximations of public strategy concepts; they are not recovered or exact implementations of commercial products.

## Safety and execution model

- Local input CSV only. No network access, download fallback, exchange SDK, CCXT, API key, account access, testnet, or order-routing adapter.
- Spot-only, long-only, cash-backed inventory. No shorts, leverage, margin, futures, borrowing, or liquidation model.
- Market orders submitted after a closed candle are eligible at the next candle open. Limit orders persist and fill only on later candle ranges that touch the limit; a favorable opening gap uses the open price.
- Market fills account for fee, half-spread, and slippage separately. Limit fills incur the fee but no second spread/slippage surcharge.
- OHLCV does not contain queue position or the intrabar price path. These fills remain deterministic model assumptions, not order-book fidelity.
- Reports always state \`mode=historical-paper\`, \`network_calls=0\`, \`live_orders_submitted=0\`, and \`EDGE=UNPROVEN\`.
- Output paths must be explicit. Existing output files are not overwritten unless \`--overwrite\` is supplied.

## Reproducible run

Use a local CSV whose exact byte-level SHA-256 is known. The REA reference dataset is:

- Symbol/market: Binance Spot BTCUSDT, 4h candles.
- Rows: 14,789.
- SHA-256: \`7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146\`.
- Primary evaluation window: \`2022-01-01T00:00:00Z\` through \`2026-09-30T20:00:00Z\` (10,404 candles).
- Baseline costs: fee 10 bps, full spread 5 bps, slippage 5 bps. Cost-stress runs multiply these by 2 and 3.

From the repository root, run:

\`\`\`powershell
python -m research.rea_engines.cli \`
  --input "C:\path\to\BTCUSDT-4h-2020-01_to_2026-09.csv" \`
  --expected-sha256 "7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146" \`
  --output "research\rea_engines\runs\strategy-comparison.json"
\`\`\`

The \`runs\` directory must already exist. The command refuses a mismatched input hash, malformed rows, an empty OOS window, a missing output directory, or an existing output file unless \`--overwrite\` is explicit.

## Strategy definitions

- **Grid:** one resting order at a time; fixed range/levels; flat-state limit buy below close; holding-state limit take-profit above all-in average entry.
- **DCA:** market initial entry; fixed-quote adverse-move limit adds up to a configured cap; limit take-profit. An adverse add can cancel the resting take-profit before placing the add.
- **Combo:** Grid entry behavior while flat, DCA management while holding.

Parameters are fixed in the evaluation configuration. There is no parameter search on OOS data. Annual subperiods reset cash independently and must not be read as a compounded multi-year portfolio path.

## Accounting

The ledger uses \`Decimal\` and cash-backed inventory. Entry costs are capitalized into inventory cost basis and allocated pro-rata when inventory is sold. Sale costs reduce proceeds. Net PnL reconciles to ending equity minus starting cash; fees, spread, and slippage are reported separately and are not deducted twice.

The quote-based buy-and-hold benchmark allocates a fixed quote amount under the same inventory/fill caps; the fixed-quantity benchmark is reported separately because exposure differs. Neither should be presented as an apples-to-apples risk-adjusted edge without further analysis.

## Tests

The new package uses only the Python standard library. Run focused tests:

\`\`\`powershell
python -m unittest discover -s tests -p "test_rea_engine_*.py" -v
python -m compileall -q research/rea_engines tests/test_rea_engine_accounting.py tests/test_rea_engine_replay.py tests/test_rea_engine_strategies.py tests/test_rea_engine_evaluation.py
\`\`\`

Verification note (14 October 2026): the SentinelX-managed interpreter lacks pytest, and the alternate local interpreter lacks sse_starlette in its ordinary test environment. Without a shim, collection of tests/test_health_readiness.py fails on that missing import; no dependency was installed. A fresh full-suite run using python -m pytest and a temporary, external sitecustomize shim for that import passed 696 tests with 93 warnings. The shim substitutes EventSourceResponse with Starlette StreamingResponse only to permit collection/tests; it does not validate actual SSE transport behavior and was not committed. Verify application startup and SSE with the real locked dependency in a correctly provisioned environment.


## Second-asset and resilience extension

- ETHUSDT Spot 4h evaluation and the 81 monthly archive checksum checks were completed against a local dataset. Raw candles, monthly archives, and large generated run outputs are deliberately not committed or redistributed by this repository update.
- Review data/binance/DATA_USE_NOTICE.md before reuse. The downloaded dataset is restricted to non-commercial, personal, non-production research under the recorded notice; commercial/productive or live use requires separate written rights.
- execution_resilience.py and tests/test_rea_engine_resilience.py implement and test explicit intrabar ambiguity, partial fills, checksummed restart, reconciliation, event ordering and idempotency. This helper remains separate from Replay.run and SENEX runtime.
- Causal market-regime classification is documented in MARKET_REGIME_CLASSIFICATION_2026-10-14.md; labels are descriptive only, and all reports declare EDGE=UNPROVEN.

## Continuation audit — causal market-regime labels (2026-10-14)

The earlier UNKNOWN_NOT_CLASSIFIED status is superseded for descriptive 4-hour BTCUSDT/ETHUSDT research labels. Added tools/classify_market_regimes.py and tests/test_rea_market_regimes.py. The classifier uses a 180-bar trailing window, +/-10% 30-day trend thresholds, and a per-asset volatility threshold estimated from valid windows ending by 2021-12-31T23:59:59Z. It labels only OOS rows from 2022-01-01 through 2026-09-30, holds the training percentile fixed, and omits windows crossing timestamp gaps rather than fabricating bars.

Outputs:
- runs/causal-regimes-BTCUSDT-4h-20261014.csv — 10,404 rows, SHA-256 ef84e553b3c73aa7c2ccebb1d06d8a1bdd03b5371a1b7ded58df8557e8f76f70.
- runs/causal-regimes-ETHUSDT-4h-20261014.csv — 10,404 rows, SHA-256 274e043654b8b7286d9d06255097cfe06e62ef7d77a99060cb39aca1c5754c46.
- runs/market-regime-counts-cross-asset-20261014.csv — annual-by-regime counts.
- Methodology: research/rea_engines/MARKET_REGIME_CLASSIFICATION_2026-10-14.md.

Focused tests: 4 passed, 1 existing pytest-asyncio deprecation warning. Labels are descriptive covariates only. They were not used to tune strategies, do not validate future returns, and do not alter EDGE=UNPROVEN. ETH reuse remains subject to the data-use notice and its non-commercial/non-production boundary.
