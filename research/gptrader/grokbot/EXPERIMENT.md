# GPTrader experiment protocol — ORDER085

STATUS=PREREGISTERED_DESIGN_ONLY

## Scientific question

Can sealed SENEX decision-time predictions support useful PAPER trading decisions beyond trivial baselines and legitimate T0 market priors, without look-ahead?

## Units and chronology

- Packet = one sealed SENEX prediction for symbol × timestamp.
- Primary window = 1h.
- Overlapping 15m packets are not independent.
- Primary L1 uses preregistered non-overlapping 1h units.
- BTC/ETH observations from the same hour are clustered together.
- Walk forward only; no random k-fold trading evaluation.
- Decision packet seals at T0.
- GPTrader decision must be durably committed before later evidence is read.
- Settlement happens later.

## Hypotheses

H0_signal: dependence-aware SENEX directional effect is not usefully different from neutral.

H1_positive: effect is positive.

H1_anti: effect is negative and stable enough to classify `SENEX_DIRECTION_ANTI_INFORMATIVE`.

H0_policy: fixed-risk GPTrader TAKE/ABSTAIN does not improve path-independent L2 value over simple fixed-risk baselines after modeled costs.

H0_incremental: uncalibrated SENEX score(s) add no incremental discrimination/value beyond a legitimate decision-time market prior.

## Score semantics

Raw `up_prob` / confidence are uncalibrated scores. Do not use Brier/ECE or make probability-space claims directly on them. Use ranking/discrimination and dependence-aware incremental comparisons. Probability calibration requires a later separately validated mapping.

## Baselines

- always_abstain
- follow_all_directional
- fixed confidence threshold
- EV-sign rule
- T0 market-prior baseline when legitimately available
- SENEX native PAPER observational control
- ChatGPT fixed-risk TAKE/ABSTAIN overlay

SIZE_SCALE is secondary exploratory only.

## Costs

Use the same preregistered PAPER fee/slippage assumptions across comparable policies. Stress at 2× fees and 2× slippage.

## Evidence gate

```text
N_resolved_independent_1h >= 600
AND
calendar_days >= 14
```

Below the gate: `INSUFFICIENT_DATA`.

## Uncertainty

Primary inference uses a preregistered hour-clustered / temporal block bootstrap or equivalent dependence-aware method. Wilson intervals may be displayed descriptively only.

## Verdict rules

- robust below-neutral direction => `SENEX_DIRECTION_ANTI_INFORMATIVE`
- neutral/no incremental evidence/no useful fixed-risk policy => `SENEX_SIGNAL_NOT_USEFUL`
- unstable to cost stress => `EXECUTION_ASSUMPTIONS_DOMINATE`
- useful signal but GPTrader selection fails => `GPTRADER_POLICY_BAD_OR_UNPROVEN`
- insufficient sample => `INSUFFICIENT_DATA`
- positive PnL alone never proves EDGE
