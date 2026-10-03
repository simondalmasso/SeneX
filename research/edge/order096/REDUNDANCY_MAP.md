# ORDER096 — Redundancy Map

## Existing SENEX information already present

Current main already contains the following information families:

| Family | Existing SENEX surface | Consequence for ORDER096 |
|---|---|---|
| short price momentum | `oracle/institutional_core.py` price_momentum and OHLCV-derived compression | generic momentum/trend indicators have a high prior of redundancy |
| 4h trend/regime | `regime_filter_4h()` / multi-candle regime logic | SuperTrend/ZLSMA/UT-style trend state must add information beyond this |
| volatility / ATR-like range | volatility features, execution/risk stress, market EV corrections | ATR-derived indicators are not novel by construction |
| orderbook imbalance | `bidask_imbalance`, orderflow, depth imbalance | Harrier/Clodds OBI is already represented; do not add a second OBI feature |
| funding / open interest | exchange connector + institutional features | external derivative-state indicators must clear this baseline |
| spread / depth / liquidity | Polymarket and exchange adapters + execution model | spread filters are execution controls, not new alpha |
| latency/slippage | market EV, execution simulator, execution fidelity | do not import another generic latency model; calibrate existing assumptions instead |
| queue-position / book walk | `backend/portfolio` execution fidelity surfaces | prediction-market backtester methodology is useful for validation, not duplicate code |
| Polymarket discovery/orderbook | `polymarket_market_adapter.py`, runtime context | cross-venue lag is testable without adding another market client |

## Candidate overlap

### High redundancy — default reject unless disagreement subset survives

- SuperTrend
- UT Bot
- Chandelier Exit
- HalfTrend
- ZLSMA

Reason: all are deterministic transforms of price and/or ATR/range. They may still act as compact regime labels, but they are not independent evidence sources.

### Medium redundancy — worth one frozen screen

- WaveTrend
- Squeeze Momentum
- VWAP + EMA9/21 bias

Why:
- WaveTrend uses normalized deviation/oscillation structure rather than only a trailing trend.
- Squeeze release encodes volatility compression/release timing; this can differ from raw momentum, but releases are sparse.
- session VWAP introduces a path-dependent volume anchor that SENEX does not explicitly represent as one feature.

### Deferred due researcher degrees of freedom

- Lorentzian Classification.

It bundles multiple common TA features, filters, ANN-style neighbor selection and tunable settings. It cannot be fairly admitted until simple constituent families are frozen and tested.

### Proprietary/opaque — not canonical candidates

- LuxAlgo Signals & Overlays
- LuxAlgo Oscillator Matrix
- LuxAlgo Price Action Concepts / SMC

Without a reproducible public implementation/data contract, these can be benchmark observations only. They cannot become canonical SENEX features merely from screenshots or paid outputs.

## Exploratory cross-venue warning

A recent-window diagnostic screen (not promotion evidence) showed unstable behavior:

- TraderSpy BTCUSDT 1h: most ATR/trend candidates were below 50% directional accuracy and negative signed-return mean.
- Bybit BTCUSDT 1h: one-bar momentum was stronger in the small recent holdout, while SuperTrend/UT/Chandelier/ZLSMA did not consistently beat it.
- TraderSpy BTCUSDT 15m: UT Bot/WaveTrend showed small positive signed-return means, but these did not persist cleanly on 1h.
- Bybit BTCUSDT 15m: Chandelier/UT Bot looked stronger in only ~79 evaluated rows — too small and venue-sensitive for inference.
- Squeeze-release observations were extremely sparse (1–7 signals in the screened holdouts), making raw hit rates meaningless.

Conclusion: the useful question is **incremental disagreement information**, not headline indicator accuracy.
