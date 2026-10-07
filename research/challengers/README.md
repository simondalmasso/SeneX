# SENEX Challenger Lab V1

Isolated research lane for testing candidate information sources and probability
calibration without changing ORDER100, H011 predictor authority, GPTrader, or any
LIVE/real-capital path.

Canonical tracker: GitHub Issue #184.

## Status

- Stage: `HISTORICAL_SYNTHETIC_ONLY`
- `ZERO_SPEND=HARD`
- `prospective_t_star=null`
- `prospective_n=null`
- maximum prospective challengers: 2
- `EDGE=UNPROVEN`
- `LIVE=false`
- `REAL_ORDERS=0`
- `CAPITAL=0`

No result produced by this directory is evidence of real market edge until a
separately preregistered prospective cohort completes.

## Why this exists

The primary question is not whether a candidate looks accurate in-sample. It is:

> Does the candidate improve proper scoring rules relative to the contemporaneous
> market prior `p_market` on causal, held-out/prospective data?

The V1 gate therefore puts leakage control and the market baseline before model
search.

## V1 challengers

### WOLFRAM_RECAL_V1

A deterministic local logit recalibrator:

`logit(p_cal) = intercept + slope * logit(p_raw)`

The implementation uses NumPy and deterministic Newton updates. Wolfram is an
independent mathematical verification surface only and is not required at
runtime.

Wolfram independently verified the Bernoulli log-loss gradient and Hessian used
by the implementation. The symbolic result is frozen in
`WOLFRAM_RECAL_V1_MATH_VERIFICATION.json`; its SHA-256 and the external skill
reference commit are pinned in the manifest.

V1 freezes a non-negative slope constraint. If the unconstrained optimum would
invert the raw ranking, the constrained convex optimum is evaluated at
`slope=0`; the recalibrator is not allowed to manufacture an anti-signal flip.

### RECENCY_CHALLENGER_V1

A deterministic market-offset model:

`logit(p_candidate) = logit(p_market) + intercept + beta * recency_features`

The feature schema is frozen in its manifest. It consumes normalized documents,
not an unrestricted browsing session.

Allowed V1 source types are zero-spend only:

- Reddit
- Hacker News
- GitHub
- public web

The last30days repository is commit-pinned as an adapter/reference only. It is
not itself a trusted probability source. Each normalized document must also bind
the frozen `SENEX_RECENCY_NORMALIZER_V1` extractor id and its exact SHA-256.
Duplicate `(source, document_id)` identities and mixed extractor hashes fail
closed before aggregation.

Forbidden inputs include:

- Polymarket odds or prediction-market prices inside recency extraction
- post-cutoff documents
- outcomes or settlements
- paid-source substitution

Missing sources become explicit missingness; they are not silently replaced.

## Market-prior residual diagnostic

Following the highest-value ARENA recommendation, V1 also includes an
interpretable historical/synthetic diagnostic:

`logit(p) = logit(p_market) + beta_senex * (logit(p_senex) - logit(p_market))`

`beta_senex=0` is exactly market-only. V1 constrains beta to be non-negative,
so an anti-signal cannot be opportunistically flipped into a winning model.
This diagnostic answers whether SENEX appears to add incremental information
to the market prior; it is evaluated purged out-of-sample fold by fold and is
not automatically admitted as a third prospective challenger.

The historical evaluator structurally rejects rows marked
`dataset_role=PROSPECTIVE`.

## Validation contract

Random K-fold is forbidden for overlapping temporal labels.

`purged_walk_forward_splits()` uses past-only expanding training sets and
retains a training row only when its label window ends strictly before the test
start minus the frozen embargo.

Primary metrics are:

- Brier score vs `p_market`
- log loss vs `p_market`
- fixed 10-bin reliability/ECE diagnostics for market and candidate

Secondary diagnostic:

- ROC AUC for market and candidate, used only to distinguish ranking/discrimination
  from calibration. AUC is never a V1 candidate-selection criterion.

A V1 candidate is eligible for selection only when both OOS deltas are
non-positive. At most two candidates may be selected. Zero candidates is a
valid result.

## Synthetic benchmark

`python -m research.challengers.synthetic_benchmark`

The deterministic synthetic fixture exists only to test leakage controls,
optimization, scoring, and candidate rejection. Its report must always carry:

- `synthetic_only=true`
- `edge_claim=NONE`
- `prospective_t_star=null`
- `prospective_n=null`

Current synthetic V1 report selected no candidates. That is expected behavior
for a gate that does not force a winner.

## Prospective receipts

The receipt schema exists now so the future information-cut contract can be
tested before collection starts. A receipt binds:

- challenger/version
- SENEX source commit
- frozen manifest SHA
- market identity
- cutoff and outcome-not-before timestamps
- `p_market`
- candidate probability
- input-feature SHA
- fitted-model SHA
- output SHA
- receipt SHA

The receipt must be durable before outcome eligibility. Defining this schema
does **not** start prospective collection.

## Deferred tools

Deliberately not in V1 candidate search:

- SHAP: provider attribution after leakage-safe validation exists
- Optuna: only with a preregistered search space
- MAPIE/conformal: V2 abstention/risk-control candidate
- vectorbt: later PAPER/cost accounting cross-check
- L2 capture: later execution research
- NannyML/Evidently: monitoring, not edge discovery

This keeps V1 degrees of freedom small and makes rejection interpretable.

## Relationship to ORDER100

No file under `research/edge/order100/` is modified by Challenger Lab.

ORDER100 remains the existing preregistered experiment. Challenger Lab does not
consume its prospective outcomes and cannot change its cohort, cutoff, metrics,
bootstrap, threshold, or verdict.

T* for Challenger Lab will be declared only after historical/synthetic
verification and explicit selection of at most two candidates.
