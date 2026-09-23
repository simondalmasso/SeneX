# ARQ2-TOP2-MISSING-SEMANTICS-009

Status: **COMPLETE**

`ROW_ID=5858` is semantically recoverable **without imputation** under the exact c7 runtime.

## Row integrity

The exact COLD row was read by `prediction_id=5858 LIMIT 1` only. Persisted payload SHA-256 and independently computed raw-payload SHA-256 are identical:

`a176dcfbc434ff3c2ab81022d194baf259a7cb8c1ac60254223d1c658be5d7e4`

Runtime provenance is exact and points to `c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a`. Wrangler reported `rows_written=0` and `changed_db=false`.

No labels, `outcomes_dual`, top-level outcome, later price, realized PnL, ORDER-007 label artifact, or ORDER-008 metric was inspected.

## Three-way semantic evidence

The exact persisted decision pipeline contains:

- `step1_market.feature_availability_v1.oi_momentum.status = MISSING`
- source `okx:public_swap_open_interest`
- `fallback_value = 0.0`
- `observed_value = null`
- `step2_features.missing_input_mask_v1.masked_features` includes `oi_momentum`
- `step2_features.pressures.oi = null`

The location distinction matters: c7 persists `feature_availability_v1` in `step1_market`, because base `decide()` stores the output of `ingest_market()` there. It does **not** duplicate this map inside `step2_features`. The mask and pressure vector are in `step2_features`.

## Exact c7 code proof

At c7:

1. `oracle_runtime/institutional_core.py:524-562` copies explicit availability and sets every non-observed feature state to numeric `0.0`.
2. Base `oracle/institutional_core.py:441-449` computes `oi_pressure = market_state["oi_momentum"] * weight`. Therefore a missing OI state entering base compression contributes exactly `0.0`.
3. Runtime wrapper `oracle_runtime/institutional_core.py:564-631` then masks unavailable pressure entries to `None`, builds `numeric_pressures` from numeric values only, and recomputes `total_pressure = sum(numeric_pressures)`.
4. Base `decide()` at lines 1091-1143 always executes `ingest_market → compress_features` and persists their outputs as `step1_market` and `step2_features`.
5. Connector `exchange_connector.py:2365-2367,2410-2417` explicitly states that a point-in-time OI value is not OI momentum and persists missing OI momentum with fallback `0.0`.

Under that exact runtime path, a persisted MISSING OI feature cannot retain a nonzero OI contribution in `total_pressure`.

## Algebra

Persisted pressures:

```text
orderflow       +0.068213
volume_delta    -0.000241
bidask          +0.019174
funding         -0.003000
oi               null
price_momentum  +0.000818
polymarket      -0.000000
```

Sum of numeric persisted pressures: `0.084964`

Persisted `total_pressure`: `0.084964`

Delta: `0.0`

`ALGEBRA_MATCH=YES`

## Verdict

`NULL_OI_SEMANTICS=MASKED_UNAVAILABLE_ZERO_EFFECTIVE_CONTRIBUTION`

`RECOVERY_VERDICT=SEMANTICALLY_RECOVERABLE_WITHOUT_IMPUTATION`

This order does **not** substitute `null → 0` in any inferential dataset and does not alter ORDER-008. A separate preregistered ORDER-010 is required before any 32/32 TOP2 ablation can use this semantic recovery rule.

EDGE remains **UNPROVEN**. LIVE=NO. REAL_ORDERS=0. CAPITAL=0.
