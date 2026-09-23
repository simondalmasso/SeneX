# ARQ2-TOP2-ABLATION-008

Status: **PERSISTENCE_INSUFFICIENT**

The frozen ORDER-007 dataset was verified byte-exact at `606eda9b102243b08fc21fafed954c1da9a22d28edbeb89054204983d7221e22`. Its deterministic non-overlap membership remains exactly 32 rows, with identity hash `0a31c990bc28313429dff96df086f6da41db448ea7f76c686f0e32fbc51cf8bd`.

## Persistence gate

COLD was read only for the 125 exact ORDER-007 `source_prediction_id` values, in chunks of 80 and 45. Both reads reported `rows_written=0` and `changed_db=false`.

- `PRESSURES_PRESENT_N=125`
- `TOP2_COMPONENTS_PRESENT_N=124` (99.2%)
- `NONOVERLAP_TOP2_COMPLETE_N=31/32`

The denominator-level ≥95% requirement passes, but the inferential requirement does not: non-overlap row `5858` has persisted `pressures.orderflow` and `pressures.funding`, while persisted `pressures.oi=null`.

Per the preregistered contract, this is a hard stop. The missing OI contribution was not reconstructed from code, later market data, current weights, or any external feed.

## Primary test

Not executed.

`AUC_FULL`, `AUC_NO_TOP2`, `DELTA_AUC_TOP2`, the paired 10,000-replicate bootstrap CI, the micro-only secondary metrics, and the permutation control remain **UNMEASURED**. Dropping row 5858 and running the test on 31 rows would change the frozen inferential population and is therefore prohibited.

`TOP2_VERDICT=UNMEASURED`.

ORDER-007 remains unchanged: AUC `0.6477732793522267`, CI95 `[0.4296824919871795, 0.8636363636363636]`, verdict `INCONCLUSIVE`. This order does not relabel that result.

## Frozen controls

TOP1 and TOP3 were not tested. Individual orderflow/funding/OI tests: 0. Parameters tuned: 0. No inversion, calibration, threshold search, weight search, new feeds, Jev, Laya, PolyDekos, or Strategy Curator execution occurred.

Strategy Curator remains backlog-only; ToolCheck recheck is `Caution 74/100`.

No CORE/model/HYP-008 mutation, deployment, LIVE action, real order, or capital action occurred. EDGE remains **UNPROVEN**.
