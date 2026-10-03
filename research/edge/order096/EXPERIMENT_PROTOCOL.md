# ORDER096 — Incremental EDGE Experiment Protocol

## Question

Do any proposed technical indicators add **incremental** information to SENEX at 15m / 1h, or are they transformed views of price/ATR/momentum already represented in the system?

The null is useful:

`H0: candidate adds no robust OOS information beyond simpler frozen baselines.`

A candidate failing H0 tests is not a failure of SENEX; it prevents feature theater.

## Information cut

For row t, every candidate may use only OHLCV bars fully closed at or before t.

Forbidden:
- future bars;
- centered windows;
- TradingView repaint/future security values;
- settlement/outcome fields;
- current data fetched after the historical T0 cut;
- threshold selection using evaluation outcomes.

A future-mutation test is mandatory: changing bars after t must not change candidate[t].

## Frozen baseline matrix v1

Implemented with fixed defaults before evaluation:

- `momentum_1`
- `supertrend_10_3`
- `chandelier_22_3`
- `zlsma_32`
- `wavetrend_10_21`
- `utbot_1_10`
- `squeeze_momentum_20`
- `squeeze_release_20`
- `vwap_ema_9_21_bias`

No parameter search is allowed in v1.

## Data hierarchy

1. Reproduce on one frozen primary BTC venue series.
2. Verify sign/coverage behavior on a second venue.
3. Only then join with SENEX T0 prediction IDs/timestamps.
4. Do not substitute one venue's future settlement for another venue's T0 price without explicit mapping.

Minimum evaluation should span multiple volatility/trend regimes. A short recent-window screen is diagnostic only.

## Chronology

Use contiguous chronological partitions:

- TRAIN: implementation sanity / no outcome-driven tuning.
- CALIBRATION: only if a candidate requires a probability mapping.
- HOLDOUT: one-touch evaluation.
- PROSPECTIVE: required before any T0 promotion.

Simple frozen directional rules do not need a trained model. Their holdout still remains untouched until definitions are frozen.

## Metrics

### Signal diagnostics

For each candidate:

- coverage / abstention rate;
- directional accuracy;
- mean and median signed next-horizon return;
- downside-tail signed return;
- turnover / flip rate;
- correlation/agreement with `momentum_1`;
- agreement with SENEX direction when joined;
- performance specifically when candidate disagrees with SENEX;
- conditional performance by a small preregistered regime set.

A high raw hit rate with tiny coverage is not enough.

### Incremental tests

A candidate is interesting only if at least one is true OOS:

1. it improves a frozen deterministic baseline on the identical eligible sample;
2. its disagreement subset contains useful information when baseline/SENEX is wrong;
3. it adds explanatory/predictive value in a preregistered low-dimensional model;
4. it identifies a regime where a simpler rule's failure rate is materially different.

The primary target is not “candidate accuracy”; it is **delta vs benchmark on matched rows**.

### Economic diagnostics

Signal evidence and economic evidence remain separate.

For each horizon, report:

- frictionless signed return;
- fixed friction ladder;
- break-even friction;
- turnover-adjusted return;
- no claim from a metric that ignores spread/fee/slippage when turnover is material.

ORDER096 does not choose a new authoritative cost model; it reuses the current SENEX/COSMIC execution assumptions once the signal screen survives.

## Multiplicity

The candidate list is compressed into families before testing.

- ATR/trailing: one family.
- momentum/trend smoother: one family.
- volume-anchor: one family.
- complex classifier: one deferred family.

Within-family comparisons are exploratory until a representative is frozen.

For final inferential claims, use the repository's existing:
- deflated Sharpe;
- probabilistic Sharpe;
- PBO;
- White Reality Check;
- Hansen SPA;
- BH / Holm multiple-hypothesis controls;

where their assumptions match the experiment.

## Overlap / dependence

15m and 1h horizon observations may overlap with the predictor cadence and are not automatically iid.

Required:
- effective-sample accounting;
- block/stationary bootstrap or equivalent dependence-aware uncertainty;
- no iid z-score presented as final evidence when rows overlap materially.

## Promotion gate

An indicator may progress from research baseline to a prospective shadow feature only if:

```text
CAUSALITY_TEST=PASS
REPRODUCIBILITY=PASS
HOLDOUT_INCREMENTAL_SIGNAL=PASS
MULTIPLE_TESTING_CONTROL=PASS
CROSS_VENUE_SANITY=PASS
COST_STRESS=PASS_OR_NOT_APPLICABLE_TO_SIGNAL_STAGE
PROSPECTIVE_PROTOCOL_DEFINED=YES
```

Promotion means **shadow capture only**, not changing SENEX decisions.

## Kill criteria

Kill or archive the candidate when any applies:

- essentially duplicates a simpler baseline and has no useful disagreement subset;
- positive result disappears OOS;
- result depends on choosing parameters after seeing holdout;
- result fails cost stress at economically relevant turnover;
- result exists only on one venue because of feed semantics;
- result depends on repaint/future data;
- result fails family-level multiple-testing control;
- result adds complexity without measurable information gain.

## Deferred candidates

### HalfTrend

Deferred until the ATR/trailing family screen determines whether another ATR/trend implementation has information value. Adding it now mainly increases trials.

### Lorentzian Classification

Deferred to phase 2 because its ANN/classification configuration and feature/filter choices materially increase model degrees of freedom. If admitted, its exact public default configuration is frozen **before** evaluation and it competes against its own simple constituent features.

### LuxAlgo invite-only products

No reverse engineering and no paid purchase in ORDER096. An opaque proprietary tool cannot become a canonical SENEX feature without a reproducible data contract and owner cost authorization.
