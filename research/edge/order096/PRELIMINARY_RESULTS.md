# ORDER096 — Preliminary Results

STATUS=EXPLORATORY_ONLY
PROMOTION_EVIDENCE=NO
PARAMETER_SEARCH=0
EDGE=UNPROVEN
SCREEN_REFRESHED=2026-10-03

## Purpose

This is a cheap sanity screen performed before spending repository complexity on the full experiment. It uses frozen default-like implementations from ORDER096 and only recent public candle windows. It is deliberately underpowered and is not a scientific claim.

The screen below supersedes the earlier pre-fidelity draft. Before refreshing these numbers, the SuperTrend baseline was corrected to use the previous trailing bands for flips, Chandelier was aligned to the public default `useClose=true`, and Squeeze Keltner range was aligned to a simple moving average of True Range. No outcome-driven parameter changes were made.

## Reproducibility

The exploratory input is frozen in:

`research/edge/order096/data/indicator_screen_v1.json`

Git blob SHA: `693044025cde8972ef3860e9bfed5d0c2a12913b`.

The deterministic evaluator is:

`research/edge/order096/evaluate_snapshot.py`

It contains no network I/O and reproduces the standalone metrics plus a same-timestamp cross-venue signal-agreement diagnostic. The frozen file records provider, exact query parameters, candle counts, and exact first/last open timestamps.

## Windows screened

- TraderSpy BTCUSDT 15m: 500 closed candles, final 40% evaluated (199 next-bar decisions).
- TraderSpy BTCUSDT 1h: 500 closed candles, final 40% evaluated (199 next-bar decisions).
- Bybit linear BTCUSDT 15m: 199 retained candles after conservatively dropping the newest unflagged candle; final 40% evaluated (79 next-bar decisions).
- Bybit linear BTCUSDT 1h: 199 retained candles after conservatively dropping the newest unflagged candle; final 40% evaluated (79 next-bar decisions).
- Binance was queried as an additional source, but the compact normalization path used in the exploratory screen did not yield a trustworthy comparable row set. No Binance conclusion is drawn.

No candidate was tuned on these windows.

## Diagnostic table

### TraderSpy 15m

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 199 | 51.3% | -0.07 bps |
| SuperTrend | 199 | 48.2% | +0.63 bps |
| Chandelier | 199 | 47.2% | +0.78 bps |
| ZLSMA | 199 | 52.3% | -0.39 bps |
| WaveTrend | 199 | 50.8% | +1.05 bps |
| UT Bot family | 199 | 51.8% | +2.19 bps |
| Squeeze momentum | 199 | 44.7% | +1.09 bps |
| Squeeze release | 6 | 33.3% | +6.00 bps |
| VWAP+EMA bias | 173 | 46.8% | +0.79 bps |

### TraderSpy 1h

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 199 | 51.3% | +2.44 bps |
| SuperTrend | 199 | 48.7% | +0.38 bps |
| Chandelier | 199 | 43.7% | -4.03 bps |
| ZLSMA | 199 | 45.2% | -2.07 bps |
| WaveTrend | 199 | 49.7% | +0.89 bps |
| UT Bot family | 199 | 47.2% | -1.62 bps |
| Squeeze momentum | 199 | 45.7% | -2.53 bps |
| Squeeze release | 5 | 40.0% | -4.55 bps |
| VWAP+EMA bias | 138 | 42.0% | -2.84 bps |

### Bybit linear 15m

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 79 | 49.4% | +0.02 bps |
| SuperTrend | 79 | 44.3% | +1.47 bps |
| Chandelier | 79 | 45.6% | +1.32 bps |
| ZLSMA | 79 | 51.9% | +0.53 bps |
| WaveTrend | 79 | 50.6% | +1.03 bps |
| UT Bot family | 79 | 57.0% | +3.20 bps |
| Squeeze momentum | 79 | 51.9% | +1.84 bps |
| Squeeze release | 3 | 33.3% | -0.08 bps |
| VWAP+EMA bias | 68 | 50.0% | +1.78 bps |

### Bybit linear 1h

| Rule | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| momentum_1 | 79 | 57.0% | +8.30 bps |
| SuperTrend | 79 | 50.6% | +3.17 bps |
| Chandelier | 79 | 39.2% | -7.07 bps |
| ZLSMA | 79 | 39.2% | -4.07 bps |
| WaveTrend | 79 | 50.6% | +2.88 bps |
| UT Bot family | 79 | 45.6% | +1.01 bps |
| Squeeze momentum | 79 | 44.3% | -5.99 bps |
| Squeeze release | 2 | 0.0% | -46.80 bps |
| VWAP+EMA bias | 55 | 45.5% | -1.77 bps |

## Disagreement diagnostic

Agreement with one-bar momentum is high enough to make redundancy a real concern, but not so high that the indicators are identical. The useful rows are therefore the disagreement rows.

Examples from the refreshed screen:

- TraderSpy 1h SuperTrend disagreed with one-bar momentum on 97 evaluated rows and was correct on 47.4% of those rows.
- TraderSpy 1h Chandelier disagreed on 101 rows and was correct on 42.6%.
- TraderSpy 1h WaveTrend disagreed on 83 rows and was correct on 48.2%.
- Bybit 1h SuperTrend disagreed on 37 rows and was correct on 43.2%.
- Bybit 1h ZLSMA disagreed on 34 rows and was correct on 29.4%.
- Bybit 15m UT Bot disagreed on 28 rows and was correct on 60.7%, but the sample is too small to promote the hypothesis.

These are descriptive diagnostics only. They are not multiplicity-controlled and the windows are short.

## What this actually says

1. There is no stable “obvious winner”.
2. ATR/trend-family rules are especially unstable across venue/horizon and remain high-redundancy candidates.
3. Squeeze release is too sparse for any hit-rate statement.
4. A candidate that looks positive on 15m can flip negative on 1h.
5. The raw TraderSpy-vs-Bybit tables use different historical spans, so their performance differences must **not** be attributed to venue. The committed evaluator separately restricts cross-venue signal-agreement diagnostics to timestamps present in both datasets.
6. The strongest small-window 1h reading in the Bybit sample remains the simple momentum baseline, which argues against adding complexity before incremental tests.
7. Positive mean signed return with sub-50% accuracy is possible because return magnitudes are asymmetric; neither metric alone establishes EDGE.

## Next valid test

Join frozen candidate signals to the exact SENEX T0 cohort by timestamp and ask:

> When SENEX and the candidate disagree, which side contains information prospectively?

That paired disagreement test has more information value than another standalone technical-indicator backtest.

## Market-prior calibration caveat discovered during ORDER096

SENEX already stores `pipeline.step2_features.up_prob` and decision-time `external_markets_v1.polymarket.up_probability`, and `paper_view.py` exposes their difference as a diagnostic.

That difference is **not yet an EDGE estimate**:

- `up_prob` is explicitly marked as an unvalidated model probability;
- the attached Polymarket context is currently a BTC 5-minute market;
- the authoritative SENEX outcome gate is 1 hour;
- subtracting probabilities with mismatched semantics/horizons is only a diagnostic divergence.

Therefore ORDER096 does not use `up_prob - market_up` as evidence of incremental 1h EDGE. A later paired prior experiment must enforce horizon/semantic compatibility or fail closed.
