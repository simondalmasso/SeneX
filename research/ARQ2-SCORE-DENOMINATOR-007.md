# ARQ2-SCORE-DENOMINATOR-007

Status: **COMPLETE**
Transport: **REMOTE_D1_WRANGLER_SELECT_ONLY**
Diagnostic only: **YES**

## Denominator

- HOT rows examined: 320
- All post-c7 candidates: 125
- Full audit N: 125
- Labelable N: 125
- Unresolved label N: 0
- Label completeness: 100.000%
- Non-overlap 1h N: 32

## Attrition

- Step2 directions: {"LONG": 57, "NEUTRAL": 26, "SHORT": 42}
- Actions: {"EXECUTE": 65, "HOLD": 60}
- Final predictions: {"FLAT": 60, "LONG": 35, "SHORT": 30}
- Raw score distribution: {"max": 0.999978, "mean": 0.6182320640000001, "min": 2.4e-05, "n": 125, "p10": 0.0059982, "p25": 0.073468, "p50": 0.825518, "p75": 0.992007, "p90": 0.9978334}
- Directional final rate: 0.52
- FLAT final rate: 0.48
- Execute rate: 0.52

## Raw score discrimination

- AUC all-opportunity non-overlap: 0.6477732793522267
- Bootstrap 95% CI: [0.4296824919871795, 0.8636363636363636]
- Rank-IC: 0.2515342877312603
- Q5-Q1 lift: 0.07142857142857145
- Raw step2 direction accuracy: 0.5769230769230769
- Score verdict: **INCONCLUSIVE**

## Selection-bias diagnostic

- AUC all opportunity: 0.6477732793522267
- AUC ORDER-004-style subset: 0.3515151515151515
- AUC selection delta: -0.2962581278370752
- N all non-overlap: 32
- N ORDER-004-style non-overlap: 26

ORDER-004 selection bias resolved by denominator accounting: **YES**.

No Brier/LogLoss, calibration, score inversion, strategy-radar candidate test,
score/gate/risk/execution mutation, D1 write, deploy, LIVE action, real order,
or capital action was performed.

EDGE remains **UNPROVEN**.
