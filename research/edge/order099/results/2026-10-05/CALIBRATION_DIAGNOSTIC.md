# ORDER099 historical calibration diagnostic — 2026-10-05

Status: DESCRIPTIVE / HISTORICAL OPENED HOLDOUT ONLY.

This analysis does not change ORDER099, ORDER100, runtime behavior, PAPER logic, BINANCE_SIM, or the scientific verdict. EDGE=UNPROVEN.

## Frozen lineage

The diagnostic was run only after verifying the exact ORDER098/099 artifacts:

- T0 predictions SHA256: 2cee4c452916e4e0a5a2bc6b23d72159a140af0de0a4d06af9cf1d35c0324329
- T0 export manifest SHA256: a6767fb71b0e734a31d8b00144d426203d1644e5e9869194b917edc3e217cf1e
- resolutions SHA256: 8673f0c2c3e93a5df30130fd20d3c6fac3f35b35b31744fe0631ed173ea9293d
- resolution manifest SHA256: 6f11efd8208868a87a11533ae190f12d07fe334a27b815b2146f5bdda193e181

The script fails closed unless it first reproduces the four primary ORDER099 HOLDOUT scores in RUN_MANIFEST.json.

## Primary reproduction

TRAIN unique markets: 2,372
HOLDOUT unique markets: 1,169

- market-only Brier: 0.1498169988344378
- market+SENEX Brier: 0.14940014582664735
- market-only log-loss: 0.4666161216746474
- market+SENEX log-loss: 0.4657511313123149

These reproduce the canonical ORDER099 result before any secondary diagnostic is emitted.

## Calibration

Ideal logistic calibration: intercept=0, slope=1.

Market-only:
- intercept: +0.1564503
- slope: 2.0108962
- ECE10: 0.1262881
- MCE10: 0.2146207

Market+SENEX:
- intercept: +0.1486067
- slope: 1.8931401
- ECE10: 0.1123973
- MCE10: 0.1991779

Both fitted probability models are descriptively under-dispersed relative to the opened HOLDOUT. Adding SENEX improves these calibration diagnostics only modestly and does not establish incremental edge.

## Low-parameter exploratory challengers

The original TRAIN was split chronologically again: inner fit 1,589 markets; inner dev 783 markets; original opened HOLDOUT 1,169 markets.

### Two-parameter recalibration

Market-only recalibrated HOLDOUT: Brier 0.1364287; log-loss 0.4293851.
Market+SENEX recalibrated HOLDOUT: Brier 0.1393747; log-loss 0.4364055.

Paired augmented-minus-market Brier delta bootstrap: mean +0.0029432; 95% CI [-0.0004605, +0.0061326].
Paired log-loss delta bootstrap: mean +0.0070874; 95% CI [-0.0005203, +0.0144214].

Interpretation: recalibration materially improves absolute probability quality, but the augmented model is descriptively worse than market-only after the same development-only recalibration discipline.

### One-parameter shrinkage

DEV-selected augmented weight: 0.69.
Opened HOLDOUT blend: Brier 0.1563960; log-loss 0.4854691.
Paired blend-minus-market Brier delta: mean -0.0007727; 95% CI [-0.0021196, +0.0006902].
Paired log-loss delta: mean -0.0016596; 95% CI [-0.0047184, +0.0014494].

The intervals cross zero and the effect remains far below the preregistered practical Brier threshold of -0.005.

## Optimizer verification

An external review proposed that ORDER099's fixed-step optimizer had under-converged. Exact-corpus comparison against the same penalized objective refuted that claim: GD and penalized Newton/IRLS coefficients/objectives agree to numerical precision and reproduce the same HOLDOUT metrics.

PR #140/#142 nevertheless hardened the generic future-research fitter for genuinely narrow feature scales. This hardening does not change ORDER099 or ORDER100 frozen results.

## Scientific conclusion

- ORDER099 remains INCREMENTAL_EDGE_NOT_DEMONSTRATED.
- EDGE=UNPROVEN.
- Do not use this opened HOLDOUT to tune a promoted model.
- Do not add Adaptive-Supertrend/FVG or other feature families to rescue the null.
- A future calibration/shrinkage challenger requires a separately frozen fresh cohort.
