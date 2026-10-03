# ORDER096 — Preliminary Results

STATUS=EXPLORATORY_ONLY  
PROMOTION_EVIDENCE=NO  
PARAMETER_SEARCH=0  
EDGE=UNPROVEN

## Purpose

This is a cheap sanity screen performed before spending repository complexity on the full experiment. It uses frozen default-like implementations from ORDER096 and only recent public candle windows. It is deliberately underpowered and is not a scientific claim.

## Windows screened

- TraderSpy BTCUSDT 15m: 500 closed candles, final 40% evaluated (199 next-bar decisions).
- TraderSpy BTCUSDT 1h: 500 closed candles, final 40% evaluated (199 next-bar decisions).
- Bybit linear BTCUSDT 15m: 200 candles, final 40% evaluated (79 next-bar decisions).
- Bybit linear BTCUSDT 1h: 200 candles, final 40% evaluated (79 next-bar decisions).
- Binance was queried as an additional source but the compact normalization path used for this screen did not produce a usable row set; no Binance conclusion is drawn.

No candidate was tuned on these windows.

## Diagnostic table

### TraderSpy 15m

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 199 | 51.8% | +0.04 bps |
| SuperTrend | 199 | 51.8% | +0.45 bps |
| Chandelier | 199 | 48.2% | +0.54 bps |
| ZLSMA | 199 | 52.8% | -0.28 bps |
| WaveTrend | 199 | 51.3% | +1.16 bps |
| UT Bot family | 199 | 52.3% | +2.30 bps |
| Squeeze momentum | 199 | 44.7% | +1.20 bps |
| Squeeze release | 5 | 80.0% | +5.92 bps |
| VWAP+EMA bias | 173 | 47.4% | +0.92 bps |

### TraderSpy 1h

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 199 | 51.3% | +2.44 bps |
| SuperTrend | 199 | 44.7% | -1.61 bps |
| Chandelier | 199 | 44.2% | -4.14 bps |
| ZLSMA | 199 | 45.2% | -2.07 bps |
| WaveTrend | 199 | 49.7% | +0.89 bps |
| UT Bot family | 199 | 47.2% | -1.62 bps |
| Squeeze momentum | 199 | 45.7% | -2.53 bps |
| Squeeze release | 7 | 57.1% | -7.06 bps |
| VWAP+EMA bias | 138 | 42.0% | -2.84 bps |

### Bybit linear 15m

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 79 | 49.4% | +0.02 bps |
| SuperTrend | 79 | 48.1% | -0.76 bps |
| Chandelier | 79 | 57.0% | +2.84 bps |
| ZLSMA | 79 | 51.9% | +0.53 bps |
| WaveTrend | 79 | 50.6% | +1.03 bps |
| UT Bot family | 79 | 57.0% | +3.20 bps |
| Squeeze momentum | 79 | 51.9% | +1.84 bps |
| Squeeze release | 1 | 100% | +6.40 bps |
| VWAP+EMA bias | 68 | 50.0% | +1.78 bps |

### Bybit linear 1h

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 79 | 55.7% | +8.22 bps |
| SuperTrend | 79 | 48.1% | +1.18 bps |
| Chandelier | 79 | 45.6% | -5.29 bps |
| ZLSMA | 79 | 38.0% | -4.15 bps |
| WaveTrend | 79 | 50.6% | +2.83 bps |
| UT Bot family | 79 | 45.6% | +0.95 bps |
| Squeeze momentum | 79 | 45.6% | -5.91 bps |
| Squeeze release | 2 | 0% | -46.80 bps |
| VWAP+EMA bias | 55 | 45.5% | -1.77 bps |

## What this actually says

1. There is no stable “obvious winner”.
2. ATR/trend-family rules are especially unstable across venue/horizon and look redundant with simpler momentum/regime information.
3. Squeeze release is too sparse for any hit-rate statement.
4. A candidate that looks positive on 15m can flip negative on 1h.
5. Venue sensitivity is material enough that single-source backtests would be dangerous.
6. The strongest small-window reading in this screen is the **simple momentum baseline** on Bybit 1h, which argues against adding complexity before incremental tests.

## Next valid test

Join frozen candidate signals to the exact SENEX T0 cohort by timestamp and ask:

> When SENEX and the candidate disagree, which side contains information prospectively?

That paired disagreement test has more information value than another standalone technical-indicator backtest.
