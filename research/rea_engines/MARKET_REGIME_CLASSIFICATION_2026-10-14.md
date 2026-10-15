# Market-regime classification ? 14 October 2026

## Purpose and boundary

Replaces the previous UNKNOWN_NOT_CLASSIFIED data-preparation label with a documented, reproducible descriptive classifier. This is not an alpha model, not an entry/exit strategy, and not evidence of edge. No strategy parameters were fit on OOS data. No network calls or orders were made.

## Frozen definitions

- Input timeframe: 4-hour spot candles; no missing candle is synthesized.
- Feature window: 180 bars (approximately 30 days). Windows crossing a timestamp gap are omitted.
- Trend: trailing 180-bar close-to-close return >= +10% is UPTREND; <= -10% is DOWNTREND; otherwise RANGE.
- Volatility: population standard deviation of trailing log returns, annualized using sqrt(6 * 365). HIGH_VOL is strictly above the asset-specific 75th percentile estimated from valid rolling volatility features ending at or before 2021-12-31T23:59:59Z; otherwise LOW_VOL.
- Output begins at 2022-01-01T00:00:00Z. Feature inputs are trailing-only. The volatility cutoff is frozen from the training window and never refit on OOS observations.
- Floating-point arithmetic is used for descriptive statistical features only, not for order sizing or monetary accounting.

## Source integrity and outputs

| Asset | Source SHA-256 | OOS rows | Output file | Output SHA-256 |
|---|---|---:|---|---|
| BTCUSDT 4h | 7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146 | 10,404 | runs/causal-regimes-BTCUSDT-4h-20261014.csv | ef84e553b3c73aa7c2ccebb1d06d8a1bdd03b5371a1b7ded58df8557e8f76f70 |
| ETHUSDT 4h | d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7 | 10,404 | runs/causal-regimes-ETHUSDT-4h-20261014.csv | 274e043654b8b7286d9d06255097cfe06e62ef7d77a99060cb39aca1c5754c46 |

Both outputs cover 2022-01-01T00:00:00Z through 2026-09-30T20:00:00Z. Annual counts are in runs/market-regime-counts-cross-asset-20261014.csv. Input SHA-256 values match the recorded datasets.

## Aggregate label counts

| Regime | BTC bars | ETH bars |
|---|---:|---:|
| UPTREND_LOW_VOL | 2,672 | 2,933 |
| UPTREND_HIGH_VOL | 0 | 10 |
| RANGE_LOW_VOL | 5,611 | 4,396 |
| RANGE_HIGH_VOL | 3 | 10 |
| DOWNTREND_LOW_VOL | 1,969 | 2,905 |
| DOWNTREND_HIGH_VOL | 149 | 150 |

These counts describe how the chosen thresholds partition these datasets. The small high-volatility counts reflect comparison to a fixed training-period percentile, not a claim that later markets were categorically safe or liquid.

## Verification

- tests/test_rea_market_regimes.py: 4 passed; one existing pytest-asyncio deprecation warning.
- The first test run correctly failed with four assertions because the classifier API was absent; after implementation, the focused command passed. The synthetic uptrend fixture threshold was adjusted to align with the declared momentum threshold.
- Tests cover OOS-only output, trend/volatility label contracts, invariance of earlier features to later candles, rejection of nonmonotonic timestamps/insufficient training observations, and omission of windows spanning a missing interval.
- CLI outputs report network_calls=0, live_orders_submitted=0, and EDGE=UNPROVEN.

## Interpretation and remaining gates

Regime labels are explanatory covariates only. No strategy performance was compared or selected based on these labels, no confidence interval or profitability claim is produced, and no prospective paper validation is complete. The BTC source is third-party market data; ETH data remains subject to the license boundary documented in data/binance/DATA_USE_NOTICE.md. Commercial/productive reuse of ETH remains blocked without separate written data rights.

## Reproduction

Run from the SENEX-REA workspace with the already-present Python interpreter and local CSVs:

python research/rea_engines/tools/classify_market_regimes.py --input PATH_TO_LOCAL_CSV --output research/rea_engines/runs/causal-regimes-SYMBOL-4h-20261014.csv --symbol SYMBOL --train-end 2021-12-31T23:59:59Z --lookback-days 30 --volatility-quantile 0.75 --trend-threshold-pct 10 --interval-hours 4

Output is refused if the destination exists unless --overwrite is explicitly passed. The tool uses local files only.
