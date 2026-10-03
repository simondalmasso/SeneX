# ORDER097 — Incremental EDGE Protocol

## Hypothesis

`H0`: after honest calibration, SENEX contains no incremental predictive information over the decision-time Polymarket 5m prior on the same BTC Up/Down 5m contracts.

`H1`: a train-only calibrated mapping of the frozen SENEX T0 score improves out-of-sample predictive scoring on the same contracts.

A null result is a successful result.

## Phase 0 — inventory

Read the local prediction journal only.

Extract rows with a valid causal T0 pair. Do not backfill missing priors from current market state.

Report:

- total prediction rows;
- rows with valid T0 pair;
- unique 5m markets;
- invalid/missing context count.

## Phase 1 — resolution join

Join separately collected Polymarket 5m resolutions by exact:

`(slug, condition_id, start_ts, end_ts)`.

No SENEX 15m/1h settlement may enter this join.

## Phase 2 — chronological split

Split by complete market groups in time order.

A single Polymarket contract may never appear in both train and holdout even if multiple SENEX predictions occurred during that contract.

Default research split: 67% earlier markets / 33% later markets.

No shuffling.

## Phase 3 — SENEX calibration

The raw `up_prob` is not accepted as P(UP).

Fit one minimal baseline mapping on TRAIN only:

`Platt/logistic(raw SENEX up score -> P(5m UP))`

No parameter search, feature search, indicator stack or regime slicing is permitted in v1.

The point is to answer one question: does the existing SENEX score contain incremental information?

## Phase 4 — paired holdout

On exactly the same holdout rows compare:

- Polymarket T0 prior;
- calibrated SENEX score.

Primary diagnostics:

- Brier score;
- log loss;
- directional accuracy.

Report deltas as:

`SENEX - MARKET`

so negative Brier/log-loss delta is better for SENEX.

These are not yet an EDGE declaration.

## Phase 5 — inference before promotion

If the simple paired holdout looks positive, the next gate is dependence-aware inference by market, not more features.

Required before an EDGE claim:

- market-cluster/block uncertainty;
- adequate number of unique markets and calendar span;
- calibration reliability;
- multiple-testing accounting if further hypotheses are added;
- prospective frozen replication;
- economic/execution layer tested separately.

## Promotion states

```text
BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED
READY_FOR_CHRONOLOGICAL_CALIBRATION
EVALUATED_HOLDOUT
INCREMENTAL_SIGNAL_CANDIDATE
PROSPECTIVE_REPLICATION_REQUIRED
```

ORDER097 itself never emits `EDGE=PROVEN`.

## Kill criteria

Stop this line if any is true:

1. valid historical T0 pairs are too sparse to form a real sample;
2. target-aligned 5m resolutions cannot be reproduced;
3. train-only calibration fails OOS;
4. paired Brier/log-loss do not improve over the market prior;
5. apparent improvement is concentrated in repeated observations of a few markets;
6. prospective replication removes the advantage.

If killed, do not compensate by adding 20 indicators. Move to the next independent hypothesis.
