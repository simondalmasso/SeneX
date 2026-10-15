# Second-asset evaluation — ETHUSDT Spot 4h

Status: completed offline historical-paper evaluation; EDGE=UNPROVEN.
Window: out of sample 2022-01-01 00:00 UTC through 2026-09-30 20:00 UTC.
Training-only range: Jan 2020–Dec 2021, 4,385 bars. Minimum observed low = 86.00 USDT; maximum observed high = 4,868.00 USDT.
OOS bars: 10,404. No parameters were selected from OOS outcomes.

## Dataset provenance

- CSV: data/binance/ETHUSDT-4h-2020-01_to_2026-09.csv
- Rows: 14,789. SHA-256: d30016ce01c969fbac61b792f0862759a787fa6d2acf74973f3cc4d1c03620a7
- Manifest: data/binance/ETHUSDT-4h-dataset-manifest.json; SHA-256: 2ba32145fcd46b8cc1cd9332b60e65054277f19dbf55249dd9194aa0da55aec4
- Source: Binance Public Data Spot 4h monthly klines, Jan 2020 to Sep 2026.
- Downloaded 81 monthly ZIPs plus official .CHECKSUM files. Every archive hash matched; one 8-hour gap at 2020-02-19 remains recorded before OOS. No candles were fabricated.
- Use restrictions: see data/binance/DATA_USE_NOTICE.md. Current Binance Vision Dataset Terms permit free non-commercial personal non-production backtesting, but do not authorize commercial product integration or live/proprietary execution. Those uses remain blocked without separate written rights.

## Frozen evaluation protocol

Candidate rules and OOS window were fixed. Grid bounds are derived only from training candles: lower = minimum training low (86.00), upper = maximum training high (4868.00); 10 levels, 100 USDT quote per order, 1% take-profit. DCA: 100 USDT initial quote, 50 USDT adds, 5% adverse step, 3% take-profit, max 3 adds. Combo uses the same Grid/DCA parameters. Starting cash 10,000 USDT; max inventory and per-fill quantity are both 0.2 ETH. Base costs: 10 bps fee, 5 bps full spread, 5 bps slippage; stress 1x/2x/3x.

## Full OOS results

| Model | Net return | Ending equity (USDT) | Max DD | Fills | Turnover (USDT) | Time in market | Fees (USDT) | Spread (USDT) | Slippage (USDT) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Grid clean-room | 0.362% | 10,036.22 | 0.457% | 44 | 4,440.66 | 3.854% | 4.44 | 0.00 | 0.00 |
| DCA clean-room | 0.560% | 10,056.05 | 1.779% | 107 | 10,670.86 | 99.635% | 10.67 | 0.95 | 1.90 |
| Combo clean-room | 0.778% | 10,077.85 | 0.455% | 53 | 5,380.51 | 21.453% | 5.38 | 0.00 | 0.00 |
| Buy & hold, 100 USDT quote | -0.280% | 9,971.95 | 0.880% | 1 | 100.00 | 99.990% | 0.10 | 0.03 | 0.05 |
| Buy & hold, 0.02 ETH | -0.209% | 9,979.11 | 0.656% | 1 | 74.48 | 99.990% | 0.07 | 0.02 | 0.04 |

Limit-order fills use the deterministic OHLCV touch rule and do not incur an additional modeled spread/slippage surcharge. Zero limit-fill spread/slippage is a simulation assumption, not proof of zero real execution cost.

## Cost stress — candidates

| Model | Multiplier | Net return | Max DD | Fills | Fees | Spread | Slippage |
|---|---|---:|---:|---:|---:|---:|---:|
| combo | 1x | 0.778% | 0.455% | 53 | 5.38 | 0.00 | 0.00 |
| combo | 2x | 0.750% | 0.457% | 53 | 10.77 | 0.00 | 0.00 |
| combo | 3x | 0.384% | 0.849% | 28 | 8.52 | 0.00 | 0.00 |
| dca | 1x | 0.560% | 1.779% | 107 | 10.67 | 0.95 | 1.90 |
| dca | 2x | 0.359% | 1.780% | 94 | 18.71 | 1.65 | 3.30 |
| dca | 3x | 0.029% | 1.859% | 74 | 21.99 | 1.88 | 3.75 |
| grid | 1x | 0.362% | 0.457% | 44 | 4.44 | 0.00 | 0.00 |
| grid | 2x | 0.332% | 0.459% | 44 | 8.88 | 0.00 | 0.00 |
| grid | 3x | 0.302% | 0.461% | 44 | 13.33 | 0.00 | 0.00 |

## Annual subperiods (not compounded)

| Model | Year | Return | Max DD | Fills | Time in market | Fees | Spread | Slippage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| combo | 2022 | 0.170% | 0.455% | 32 | 52.922% | 3.25 | 0.00 | 0.00 |
| combo | 2023 | 0.000% | 0.000% | 0 | 0.000% | 0.00 | 0.00 | 0.00 |
| combo | 2024 | 0.266% | 0.035% | 12 | 2.186% | 1.23 | 0.00 | 0.00 |
| combo | 2025 | 0.361% | 1.021% | 25 | 50.457% | 2.53 | 0.00 | 0.00 |
| combo | 2026 | 0.072% | 0.958% | 5 | 82.234% | 0.50 | 0.00 | 0.00 |
| dca | 2022 | -1.464% | 1.779% | 11 | 99.863% | 0.95 | 0.08 | 0.15 |
| dca | 2023 | 1.011% | 0.415% | 76 | 98.630% | 7.65 | 0.75 | 1.50 |
| dca | 2024 | 0.944% | 0.961% | 84 | 98.452% | 8.35 | 0.85 | 1.70 |
| dca | 2025 | -0.050% | 1.440% | 53 | 99.087% | 5.22 | 0.50 | 1.00 |
| dca | 2026 | -0.174% | 1.243% | 13 | 99.695% | 1.16 | 0.12 | 0.25 |
| grid | 2022 | 0.362% | 0.457% | 44 | 18.311% | 4.44 | 0.00 | 0.00 |
| grid | 2023 | 0.000% | 0.000% | 0 | 0.000% | 0.00 | 0.00 | 0.00 |
| grid | 2024 | 0.011% | 0.001% | 2 | 0.046% | 0.20 | 0.00 | 0.00 |
| grid | 2025 | 0.202% | 0.157% | 42 | 6.941% | 4.22 | 0.00 | 0.00 |
| grid | 2026 | 0.124% | 0.175% | 26 | 28.999% | 2.62 | 0.00 | 0.00 |
| buy_and_hold_fixed_qty | 2022 | -0.507% | 0.587% | 1 | 99.954% | 0.07 | 0.02 | 0.04 |
| buy_and_hold_fixed_qty | 2023 | 0.217% | 0.117% | 1 | 99.954% | 0.02 | 0.01 | 0.01 |
| buy_and_hold_fixed_qty | 2024 | 0.212% | 0.367% | 1 | 99.954% | 0.05 | 0.01 | 0.02 |
| buy_and_hold_fixed_qty | 2025 | -0.078% | 0.459% | 1 | 99.954% | 0.07 | 0.02 | 0.03 |
| buy_and_hold_fixed_qty | 2026 | -0.060% | 0.364% | 1 | 99.939% | 0.06 | 0.01 | 0.03 |
| buy_and_hold_quote_100 | 2022 | -0.681% | 0.788% | 1 | 99.954% | 0.10 | 0.03 | 0.05 |
| buy_and_hold_quote_100 | 2023 | 0.909% | 0.488% | 1 | 99.954% | 0.10 | 0.03 | 0.05 |
| buy_and_hold_quote_100 | 2024 | 0.466% | 0.804% | 1 | 99.954% | 0.10 | 0.02 | 0.05 |
| buy_and_hold_quote_100 | 2025 | -0.116% | 0.683% | 1 | 99.954% | 0.10 | 0.03 | 0.05 |
| buy_and_hold_quote_100 | 2026 | -0.100% | 0.611% | 1 | 99.939% | 0.10 | 0.02 | 0.05 |

Every annual slice resets cash independently and must not be compounded. Market-regime labels remain UNKNOWN_NOT_CLASSIFIED; they were not invented or inferred post hoc.

## Interpretation limits

This checks whether clean-room rules run on a second spot series; it is not proof of robust edge. ETH uses a training-derived price grid and a different base-unit cap than the BTC report, so raw returns are not risk-equivalent. OHLCV cannot reveal queue priority, intrabar path, partial-fill probability or book depth. There is no prospective paper cohort. EDGE=UNPROVEN.
