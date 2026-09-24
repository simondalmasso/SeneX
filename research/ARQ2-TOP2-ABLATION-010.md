# ARQ2-TOP2-ABLATION-010

Status: **COMPLETE**

This order resumes the exact preregistered TOP2 ablation from ORDER-008 using the semantic recovery proved by ORDER-009. ORDER-008 remains **PERSISTENCE_INSUFFICIENT** and is not reclassified.

## Frozen population and transport

- Dataset SHA-256: `606eda9b102243b08fc21fafed954c1da9a22d28edbeb89054204983d7221e22`
- Deterministic non-overlap N: `32`
- Membership SHA-256: `0a31c990bc28313429dff96df086f6da41db448ea7f76c686f0e32fbc51cf8bd`
- COLD requested: `32`
- COLD received: `32`
- HOT scans: `0`
- D1 writes: `0`

The single COLD SELECT contained only the exact 32 frozen `source_prediction_id` values. Wrangler reported `rows_written=0` and `changed_db=false`.

## Runtime-semantic recovery

Exactly one exception was used:

```text
source_prediction_id=5858
persisted pressures.oi=null
effective_oi_pressure=0.0
RECOVERED_BY_RUNTIME_SEMANTICS=YES
```

The 009 proof was revalidated from the same persisted COLD audit: `oi_momentum.status=MISSING`, `fallback_value=0.0`, the missing-input mask contains `oi_momentum`, `pressures.oi=null`, exact c7 provenance is present, and persisted `total_pressure` equals the sum of the remaining numeric pressures. No other null or missing component was substituted.

This is not imputation.

## Frozen formula

```text
FULL_SCORE      = total_pressure
TOP2_PRESSURE   = orderflow_pressure + funding_pressure + effective_oi_pressure
NO_TOP2         = total_pressure - TOP2_PRESSURE
MICRO_ONLY      = TOP2_PRESSURE
```

No sigmoid, normalization, rescaling, winsorization, sign change, score inversion, fitted weight, threshold tuning, or component selection was performed.

## Primary paired ablation

Same 32 rows. Same paired bootstrap design.

```text
AUC_FULL                 = 0.6477732793522267
AUC_NO_TOP2              = 0.6396761133603239
DELTA_AUC_TOP2           = 0.0080971659919028
DELTA_AUC_CI95           = [-0.059523809523809534, 0.07450980392156858]
BOOTSTRAP_VALID_REPLICATES = 10000
SEED                     = 20260923
```

The 95% paired bootstrap interval crosses zero.

`TOP2_VERDICT=INCONCLUSIVE`

The current `orderflow + funding + OI` block therefore does **not** show statistically resolved positive marginal contribution on this frozen cohort, but it is also **not falsified** as a positive contribution because the interval includes both negative and positive values.

## Secondary descriptive metrics

```text
AUC_MICRO_ONLY               = 0.6477732793522267
RANK_IC_MICRO_ONLY           = 0.2515342877312603
TOP2_MEAN_ABS_CONTRIBUTION   = 0.63926425
TOP2_SIGN_AGREEMENT_RATE     = 1.0
TOP2_SIGN_FLIP_RATE          = 0.0
PERMUTED_DELTA_AUC           = 0.0
```

The permutation result is a negative-control diagnostic only, not a p-value or promotion gate.

## Scientific interpretation

ORDER-007 remains unchanged: AUC `0.6477732793522267`, CI95 `[0.4296824919871795, 0.8636363636363636]`, verdict `INCONCLUSIVE`.

TOP1 and TOP3 were not tested. No orderflow/funding/OI component was tested individually. No component-specific outcome slices, regime splits, weight tuning, score inversion, interactions, threshold changes, calibration, new feeds, Strategy Curator, Jev, or Laya were used.

Because TOP2 is **INCONCLUSIVE**, any next test must use an independently preregistered prospective extension on new observations. No architecture change is justified from this cohort.

EDGE remains **UNPROVEN**. LIVE=NO. REAL_ORDERS=0. CAPITAL=0.
