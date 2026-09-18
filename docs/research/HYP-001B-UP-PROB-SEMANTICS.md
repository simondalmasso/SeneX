# HYP-001B — `step2_features.up_prob` Mathematical Semantics Audit

Status: **RETAIN for prospective calibration research only.**
Probability/edge status remains **UNPROVEN**.

## What `up_prob` is today

The current runtime computes a scalar directional pressure and then applies a fixed logistic squashing function:

```text
up_prob   = sigmoid(5 * total_pressure)
down_prob = sigmoid(-5 * total_pressure)
          = 1 - up_prob
```

The base pressure is the sum of observed weighted engineered signals:

```text
orderflow * 1.00
+ (volume_delta * price_momentum) * 0.60
+ bidask_imbalance * 0.35
+ funding_signal * 0.30
+ oi_momentum * 0.40
+ price_momentum * 0.50
```

The runtime missing-input mask removes unavailable feature pressures from the numeric sum. Noise and agreement affect `conviction`, but they do **not** transform `up_prob` into a calibrated probability.

When the explicit PAPER-only Polymarket directional experiment is enabled and the Polymarket context is eligible, the real-market wrapper can use:

```text
total_pressure = base_total_pressure + 0.25 * polymarket_directional_pressure
```

Otherwise that Polymarket component has effective weight zero. Any future calibration cohort must freeze or explicitly stratify this state.

## What it is not

`up_prob` is not currently:
- an empirically calibrated `P(BTC up in 1h)`
- a posterior probability
- fitted by minimizing a proper probability scoring rule
- equivalent to persisted `confidence`
- equivalent to the final LONG action probability

Persisted `confidence` comes from `conviction = |up_prob-down_prob| * (1-noise)` and is already labelled `RAW_CONVICTION / UNVALIDATED`.

Final direction also applies pressure thresholds and can suppress LONG under a bearish 4h regime, so `up_prob` is upstream of the final action decision.

## Can it become a calibrated probability prospectively?

**Yes, as a candidate score, conditionally.** The score is deterministic, bounded, and monotonic in `total_pressure`, which makes prospective recalibration mathematically possible. This does not mean it is calibrated now.

Pre-registered target for the first native SENEX calibration study:

```text
Y_up_native = 1 if proof-qualified price(t+3600) > price_now(t), else 0
```

This is deliberately **not** called the Polymarket outcome. Equality maps to 0 because the event being estimated is strict-up. Consequently a calibrated `P(Y_up_native=1)` must not automatically make the existing raw `down_prob` a calibrated strict-down probability.

Candidate first mapping:

```text
x = logit(raw_up_prob)
p_cal = sigmoid(alpha + beta * x)
```

This is Platt-style recalibration. `alpha` and `beta` must be fitted only on a future independent calibration block.

## Required prospective design

1. Registration timestamp precedes every calibration row.
2. Use only proof-qualified native SENEX 1h outcomes.
3. Use deterministic non-overlapping 1h observations.
4. Freeze code hash, config hash, effective weights hash and feature-availability policy; otherwise start a new cohort/version.
5. Freeze/stratify `exchange_used`; do not silently pool changing venue semantics.
6. Calibration block occurs strictly before validation block.
7. Fit mapping on calibration block only.
8. Validation block must be untouched during fitting/model selection.
9. No retroactive relabeling, threshold tuning or cherry-picking.
10. Raw `up_prob` remains stored unchanged.

## Metric gate

**Brier and LogLoss remain prohibited now.**

They become eligible only for the calibrated probability on the untouched forward validation cohort after target semantics, mapping version and calibration cohort are frozen. Raw historical `up_prob` is never retroactively relabelled as calibrated.

## Verdict

`RETAIN` means only: the scalar score has enough mathematical structure to justify a prospective calibration experiment.

It does **not** mean:
- calibrated probability demonstrated
- predictive edge demonstrated
- Polymarket edge demonstrated
- economic edge demonstrated
- production promotion authorized
