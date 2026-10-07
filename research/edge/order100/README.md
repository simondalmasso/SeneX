# ORDER100 — Prospective Low-Disagreement Confirmation

Research/PAPER only.

ORDER099 historical evaluation concluded:

```text
INCREMENTAL_EDGE_NOT_DEMONSTRATED
```

The only corrected exploratory pattern worth a fresh test was that any small
incremental SENEX benefit appeared when SENEX was close to the contemporaneous
Polymarket prior, while the highest-disagreement quartile was adverse.

ORDER100 freezes that hypothesis **before** future outcomes.

## Frozen prospective boundary

Only exact markets with:

```text
market_start_ts >= 1791090000
UTC >= 2026-10-04T05:00:00Z
```

are admissible.

## Frozen primary band

```text
abs(senex_raw_up - p_market) <= 0.17693099999999995
```

One earliest causal T0 row per exact market.

## Frozen models

Market-only:

```text
intercept = -0.029977798153386935
market_logit = 0.4346033993273502
```

Market+SENEX:

```text
intercept = -0.016707909800067956
market_logit = 0.42444216403460056
senex_logit = 0.04187479127974901
```

No future outcome may refit these coefficients.

## Evidence floor

At least 300 unique resolved prospective markets inside the frozen low-
disagreement band.

Final evaluation:

- Brier delta;
- log-loss delta;
- 10,000 unique-market bootstrap replicates;
- seed 7;
- 95% interval.

Prospective confirmation requires all:

1. mean Brier delta <= -0.005;
2. Brier CI upper bound < 0;
3. log-loss CI upper bound < 0;
4. >=300 unique resolved prospective markets;
5. artifact lineage and anti-circularity checks pass.

Otherwise:

```text
PROSPECTIVE_INCREMENTAL_EDGE_NOT_CONFIRMED
```

Null is an admissible successful result.

## Frozen safety secondary

Descriptive only:

```text
abs(senex_raw_up - p_market) > 0.60501925
```

This is tracked because ORDER099 found the highest-disagreement quartile
adverse. It cannot change runtime behavior under ORDER100.

## Run

Use fresh ORDER098-compatible T0 + resolution artifacts and their manifests:

```bash
python -m research.edge.order100.prospective_low_disagreement \
  --predictions /data/datasets/order100/t0_predictions.jsonl \
  --resolutions /data/datasets/order100/resolutions.jsonl \
  --predictions-manifest /data/datasets/order100/t0_export_manifest.json \
  --resolutions-manifest /data/datasets/order100/resolution_manifest.json \
  --persistence-receipts /data/datasets/order100/t0_predictions.persistence_receipts.jsonl \
  --bootstrap 10000 \
  --seed 7
```

The evaluator contains no fitting path.

The persistence receipt path must be the immutable receipt snapshot emitted
alongside the prospective ORDER098 v2 prediction artifact, not the live
append-only runtime ledger.

## Prohibited

- no threshold tuning after prospective data begin;
- no feature additions to rescue a null;
- no HMM/regime rescue;
- no SHORT inversion;
- no prospective-outcome refit;
- no deployment/LIVE/order/capital promotion from ORDER100 alone.

Issue: #115.
