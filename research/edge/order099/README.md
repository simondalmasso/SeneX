# ORDER099 — Tabular Falsification Before HMM

Research-only extension of ORDER097.

## Lineage

As of 2026-10-04, ORDER097 and ORDER098 are merged into `main`. ORDER099 is
therefore evaluated directly against canonical `main`; it does not vendor or
fork either dependency.

## Question

Does frozen SENEX decision-time information add stable out-of-sample information
beyond the contemporaneous Polymarket BTC Up/Down 5m prior?

ORDER099 deliberately asks this with simple tabular/statistical tools before any
HMM or regime model is allowed.

## Inputs

Two immutable, secret-free artifacts produced by ORDER098:

```text
t0_predictions.jsonl
resolutions.jsonl
```

Keep their manifests and SHA256 hashes beside them.

ORDER099 verifies both manifests before analysis: exact file SHA256s, per-row
source-audit hash lineage, resolution-record hash lineage, prediction→resolution
dataset binding, and complete resolution coverage. Partial/rejected corpora fail
closed before the holdout is evaluated.

The T0 file must contain the exact persisted decision-time audit projection.
The resolution file must contain exact target-aligned public Polymarket labels.
Do not reconstruct historical priors from current/live data.

## Pre-registered analysis

ORDER099 reuses ORDER097 for:

- exact BTC 5m target identity;
- anti-circularity guard;
- post-close resolution provenance;
- label-availability purge;
- chronological train/holdout split;
- equivalently trained market-only and market+SENEX logistic models.

It adds:

- one descriptive row per unique market;
- TRAIN-only quartile boundaries;
- TRAIN-only disagreement threshold;
- fixed UTC session buckets: 00-05 / 06-11 / 12-17 / 18-23;
- unique-market cluster bootstrap uncertainty;
- deterministic final verdict.

The final run uses 10,000 bootstrap replicates with seed 7 unless a future
preregistered order changes those values before results are observed.

ORDER099 also requires at least **200 unique resolved markets** before it can
emit an edge verdict. Smaller datasets return `BLOCKED_DATA / EDGE=UNPROVEN`;
the ORDER097 8-row TRAIN minimum remains only a technical optimizer floor.

## Market weighting

The unit of evidence is one unique Polymarket market, not one prediction row.
If multiple valid SENEX T0 rows exist for the same market, ORDER099 keeps only
the earliest causal decision-time row for model fitting, holdout scoring and
tabular summaries. This prevents markets with more prediction rows from
receiving extra statistical weight.

## Exploratory subgroup inference

Pre-registered holdout cells are tested only when `n>=30`. For each eligible
cell, ORDER099 runs a one-sided exact sign test on per-market Brier loss deltas
and applies the existing SENEX Holm-Bonferroni and Benjamini-Hochberg
corrections across the complete eligible subgroup family.

Subgroup findings are exploratory. They cannot override the primary nested
holdout + cluster-bootstrap verdict.

## Edge rule

Let delta mean:

```text
loss(market + SENEX) - loss(market-only)
```

Negative is better.

`EDGE_SUPPORTED` requires all of the following:

1. mean Brier delta <= -0.005;
2. 95% market-cluster bootstrap upper bound for Brier delta < 0;
3. 95% market-cluster bootstrap upper bound for log-loss delta < 0.

Otherwise:

```text
INCREMENTAL_EDGE_NOT_DEMONSTRATED
```

This is intentionally conservative. The result is research evidence, not a
LIVE/capital authorization.

## Intern Discovery CPU run

No GPU is required.

Recommended persistent layout:

```text
/data/SeneX
/data/datasets/senex-order098
/data/results/senex-order099
```

Use the exact ORDER099 branch/head:

```bash
cd /data/SeneX
git fetch origin
git checkout order099/tabular-falsification
git rev-parse HEAD
python -m pip install -r senecio_polymarket/requirements.lock
```

Place the verified ORDER098 artifacts under:

```text
/data/datasets/senex-order098/t0_predictions.jsonl
/data/datasets/senex-order098/t0_export_manifest.json
/data/datasets/senex-order098/resolutions.jsonl
/data/datasets/senex-order098/resolution_manifest.json
```

Then run:

```bash
mkdir -p /data/results/senex-order099

python -m research.edge.order099.tabular_falsification \
  --predictions /data/datasets/senex-order098/t0_predictions.jsonl \
  --predictions-manifest /data/datasets/senex-order098/t0_export_manifest.json \
  --resolutions /data/datasets/senex-order098/resolutions.jsonl \
  --resolutions-manifest /data/datasets/senex-order098/resolution_manifest.json \
  --bootstrap 10000 \
  --seed 7 \
  | tee /data/results/senex-order099/report.json
```

Persist alongside the report:

```bash
git rev-parse HEAD > /data/results/senex-order099/git_sha.txt
python --version > /data/results/senex-order099/python_version.txt
python -m pip freeze > /data/results/senex-order099/environment.txt
sha256sum \
  /data/datasets/senex-order098/t0_predictions.jsonl \
  /data/datasets/senex-order098/t0_export_manifest.json \
  /data/datasets/senex-order098/resolutions.jsonl \
  /data/datasets/senex-order098/resolution_manifest.json \
  /data/results/senex-order099/report.json \
  > /data/results/senex-order099/sha256sums.txt
```

## HMM prohibition

No HMM/regime model belongs in ORDER099.

SENEX already contains a heuristic HMM overlay, but it is not an admissible
explanation or rescue mechanism for this experiment. A later regime experiment
is justified only after a simpler base model demonstrates reproducible
incremental information and preregistered tabular heterogeneity survives OOS
uncertainty/multiple-testing controls.

## Interpretation

A null result is a valid successful experiment.

Do not add features after seeing the holdout merely to rescue a failed result.
Do not tune SHORT here; the current 1h directional authority and this exact 5m
Polymarket target are different scientific questions.
