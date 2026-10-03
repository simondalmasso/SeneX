# ORDER096 — Final Recommendation

STATUS=RESEARCH_SCREEN_COMPLETE
EDGE=UNPROVEN
RUNTIME_CHANGE=NO
PREDICTOR_CHANGE=NO
GPTRADER_CHANGE=NO

## Current conclusion

The proposed indicator set does **not** justify expanding SENEX's production feature surface.

The first-principles reason is redundancy: SENEX already contains price momentum, 4h regime/trend, volatility/range, orderbook imbalance, funding/open-interest, spread/depth and execution-aware context. Most of the proposed TradingView indicators are deterministic recombinations of those same families.

The recent cross-venue screen reinforces that conclusion: no classic indicator produced a stable advantage across 15m/1h and TraderSpy/Bybit. Some candidates looked positive in one small window and negative in another. That is evidence against immediate integration, not proof that every candidate is useless.

## What survives

### 1. Keep as research baselines, not production features

- WaveTrend
- Squeeze Momentum / release
- UTC-session VWAP + EMA9/21 bias
- one representative ATR/trailing family baseline

Their purpose is to answer whether SENEX is missing information, not to decorate the predictor.

### 2. Highest-value new strategy hypothesis

**Spot→Polymarket lag residual.**

This is more promising than adding more chart indicators because it tests market inefficiency rather than another transform of BTC price.

SENEX already has most required surfaces:
- spot / derivative market data;
- Polymarket market and orderbook context;
- timestamps;
- spread/depth;
- time-to-close context.

What is missing is a calibrated residual between the observed prediction-market probability and the move implied by the contemporaneous underlying market.

This should become a separate preregistered shadow strategy experiment, not a live feature.

### 3. Highest-value execution improvement

Do not add another backtesting/execution engine.

Use the external prediction-market backtesting research as a methodology reference and calibrate SENEX's existing:
- latency;
- slippage;
- queue-position;
- fill probability;
- quote-age assumptions

against observed PAPER logs.

This directly strengthens ECONOMIC_EDGE without feature bloat.

### 4. Secondary strategy candidate

**Penny-clipper / oscillation maker strategy** may be distinct enough to test because its proposed edge is microstructure/fill economics rather than directional prediction.

It should be evaluated only on L2 replay with passive-fill realism. A backtest that grants fills at touch is invalid.

## What does not enter SENEX now

- SuperTrend as a production feature.
- UT Bot as a production feature.
- Chandelier Exit as a production feature.
- HalfTrend as another ATR/trend duplicate.
- ZLSMA as another generic trend feature.
- Lorentzian Classification before its constituent/simple baselines are exhausted.
- LuxAlgo paid/opaque outputs.
- market-making framework.
- generic DCA.
- copy-trading.
- weather trading.
- a second risk engine.
- a second execution engine.
- a 107GB production data import.
- another AI agent/MCP layer.

## Indicator promotion rule

No candidate is promoted from ORDER096 merely because its standalone hit rate exceeds 50%.

A candidate must demonstrate one of:

1. positive paired delta against the simplest frozen baseline on identical rows;
2. useful information specifically on SENEX disagreement rows;
3. reproducible regime separation not already captured by SENEX;
4. prospective OOS improvement after multiplicity control.

Then it becomes a **shadow feature first**.

It does not alter the SENEX score until a later explicit order.

## Recommended next experiments

### EXP-096-A — SENEX disagreement matrix

For every exact T0 SENEX prediction, compute the frozen ORDER096 signals from only data available at T0.

Primary table:

`prediction_id | SENEX_direction | candidate_direction | agree/disagree | 1h outcome | signed return | regime`

Primary question:

> When candidate and SENEX disagree, is the candidate systematically informative?

This has higher information value than standalone indicator backtests.

### EXP-096-B — Spot→Polymarket lag

Freeze:
- spot move windows;
- token mid;
- time-to-expiry;
- spread/depth;
- quote age.

Fit/calibrate only on historical resolved rows, then test prospectively.

Do not import the rough Clodds fair-value scaling.

### EXP-096-C — execution parameter calibration

Use PAPER execution observations to estimate:
- latency distribution;
- spread crossing;
- slippage conditional on volatility/depth;
- passive fill probability / queue proxy.

Compare the calibrated execution model to current static assumptions.

### EXP-096-D — penny-clipper replay

Only after C, because this strategy's economics depend on maker-fill realism.

## Kill criteria

Kill an indicator hypothesis if:
- it is >85% agreement with a simpler rule and the disagreement subset has no positive incremental information;
- its sign/benefit is venue-specific with no defensible microstructure reason;
- it fails chronological OOS;
- it only becomes positive after parameter search on holdout;
- it does not survive family-level multiplicity control;
- its expected economic benefit is smaller than execution-model uncertainty.

Kill the spot→Polymarket lag hypothesis if:
- the residual has no prospective relationship to later token repricing or settlement;
- edge disappears after quote age, spread, depth and time-to-expiry controls;
- the effect exists only with timestamp alignment that would not have been executable.

## Exact next implementation order

Do **not** modify the production predictor.

After ORDER096 CI:

1. keep the deterministic baselines under `research/edge/order096/`;
2. build a timestamp joiner from sealed SENEX T0 rows to frozen OHLCV snapshots;
3. generate the paired disagreement matrix;
4. run the existing statistical-validation battery;
5. if and only if one family survives, propose a prospective shadow feature order;
6. separately design `HYP_PM_LAG001` for spot→Polymarket lag.

## Final disposition

```text
ADD_MORE_INDICATORS_TO_PRODUCTION=NO
KEEP_RESEARCH_BASELINES=YES
BEST_NEW_EDGE_HYPOTHESIS=SPOT_TO_POLYMARKET_LAG
BEST_EXECUTION_UPGRADE=CALIBRATE_EXISTING_EXECUTION_MODEL_FROM_PAPER_LOGS
PENNY_CLIPPER=PHASE_2_REPLAY_ONLY
ORDERBOOK_IMBALANCE=ALREADY_HAVE
GENERIC_LATENCY_MODEL=ALREADY_HAVE
COPY_TRADING=DEFER
MARKET_MAKING=DEFER
DCA=REJECT_FOR_EDGE_CALIBRATION
EDGE=UNPROVEN
```


## 2026-10-03 additional-source ruling

The second research wave does not change the production recommendation.

### Do not integrate

- NVIDIA OpenShell into the current H011 runtime — useful future sandbox pattern, but unnecessary control-plane/runtime complexity while collectors remain dormant and bridge-isolated.
- Decision 2.0 models — no demonstrated SENEX information gap they solve; adds inference and custom-code surface before EDGE is established.
- Prism Legal OS — unrelated mission.
- awesome-pinescript — catalog only.
- Tech Leadership Map+ — US-equity leadership context is a separate cross-asset hypothesis, not a free BTC feature.
- Minicharts Pro+ — visualization only.

### Tested, not promoted

Minimal completed-HTF range-location and sweep/reclaim baselines were added to ORDER096. Their recent TraderSpy/Bybit 15m/1h screen is weak/unstable and does not justify production integration.

Therefore the surgical rule remains:

```text
ADD_FULL_HTF_INDICATOR_STACK=NO
KEEP_COMPLETED_HTF_PRIMITIVES_AS_RESEARCH_BASELINES=YES
NEW_RUNTIME_DEPENDENCIES=0
NEW_PAID_COST=0
EDGE=UNPROVEN
```


## User-supplied iFVG confluence source — surgical ruling

The Google Doc source `Pastebin INFO SENEX` was read directly and decomposed rather than ported.

Its six-check setup grade combines liquidity sweep, HTF FVG/PDA delivery, delta imbalance, IFVG inversion, clear targets and SMT divergence. Most of that stack is redundant with information SENEX already carries or is strategy packaging rather than a new information source.

Three concepts are genuinely distinct enough to retain as research hypotheses:

- IFVG inversion;
- confirmed liquidity sweep/reclaim with detection-time timestamps;
- BTC↔ETH SMT divergence.

AUD ran cheap causal proxies on recent 15m/1h closed BTC/ETH bars only to decide whether immediate implementation was warranted. Results were sparse and unstable across horizons: no concept earned production complexity. Full source audit: `IFVG_SOURCE_AUDIT.md`.

Therefore:

```text
PORT_FULL_IFVG_SCRIPT=NO
ADD_CONFLUENCE_GRADE=NO
ADD_SECOND_DELTA_FEATURE=NO
KEEP_IFVG_SWEEP_SMT_AS_RESEARCH_HYPOTHESES=YES
PROMOTE_NOW=NO
```

This is consistent with FEATURE_FREEZE: extract hypotheses, not stacks.
