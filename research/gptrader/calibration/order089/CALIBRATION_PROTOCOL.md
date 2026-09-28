# ORDER089 Calibration Protocol

## Scope

This protocol calibrates a vendor-neutral Decision-Agent policy around sealed SENEX T0 packets. It does not calibrate SENEX model weights and does not embed any LLM vendor in the server contract.

Direction remains SENEX-owned. The client may return TAKE or ABSTAIN only. Calibration is a deterministic abstention gate: it may convert a client TAKE to ABSTAIN when preregistered T0-only conditions fail. It may never create a TAKE from an ABSTAIN, flip direction, alter SENEX model weights, add features, or read current/future/outcome state at decision time.

## Calibration unit

The unit is a versioned, deterministic Decision-Agent calibration artifact applied to the canonical DecisionEnvelope contract.

The artifact is model-agnostic:
- `calibration_id` is derived from calibration core fields, not model/vendor identity.
- `agent_id`, `model_provider`, `model_name`, and `policy_version` are evaluation provenance only.
- a deterministic non-LLM agent uses the same contract.
- changing the agent does not require a new MCP/T0/H2/persistence design.

Scientific evidence does not automatically transfer across a model replacement. The same fixed artifact may be reused operationally, but the new agent must accumulate its own shadow/holdout provenance before claims are made.

## Search space v1

Candidate enumeration is fixed before calibration outcomes are inspected. Maximum candidate count: 8.

Candidates use thresholds derived from TRAIN T0 distributions only:

1. BASELINE: no additional calibration gate.
2. CONF_Q60
3. CONF_Q70
4. CONF_Q80
5. EV_Q60, only when EV is present in the sealed T0 contract.
6. EV_Q70, only when EV is present.
7. EV_Q80, only when EV is present.
8. CONF_Q70_AND_EV_Q70, only when both fields are present.

Quantiles are computed from TRAIN T0 values, not outcomes. CALIBRATION outcomes select among these already-enumerated candidates. If an input field is absent, its candidates are ineligible rather than imputed.

No symbol/hour regime mask enters v1. A future regime mask requires a new search-space version and preregistration before outcomes are inspected.

## Parameters allowed

- minimum SENEX confidence rank/quantile using a T0 field already present;
- minimum EV rank/quantile using a T0 field already present;
- deterministic abstention gate combining those preregistered thresholds;
- future fixed T0-only regime masks only under a new preregistered protocol version.

## Parameters forbidden

- SENEX direction;
- model weights;
- learned features or embeddings;
- current price or market plugins at decision time;
- settlement/outcome-derived state;
- unrestricted prompt search against holdout outcomes;
- FLIP;
- broker/wallet/signer/LIVE capability;
- D1 reads/writes;
- adaptive production refits;
- risk scaling in ORDER089 v1, unless a separate PAPER contract explicitly authorizes it.

## Candidate identity

Canonicalize the artifact core with sorted JSON keys and UTF-8 encoding.

`calibration_id = "cal089_" + sha256(canonical_core_json)`

The core includes source epoch hashes, TRAIN/CALIBRATION boundaries, parameter definitions, frozen fields, objective, constraints, code SHA, protocol version, and random seed. Agent/model provenance is stored with replay/evaluation records and is excluded from `calibration_id`.

## Offline replay contract

For each eligible sealed T0 packet:

1. validate packet schema/hash/provenance and quarantine exclusion;
2. materialize the same immutable T0 packet for every arm;
3. produce and freeze all arm decisions before settlement is read;
4. only then attach settlement/outcome;
5. aggregate first by independent 1h cluster, with same-hour BTC/ETH in the same cluster.

Arms:
- CONTROL: existing fixed-risk SENEX directional control.
- DECISION_AGENT: uncalibrated TAKE/ABSTAIN client.
- CALIBRATED_AGENT: the same client plus the frozen deterministic calibration gate.

The replay engine is read-only. It may not write runtime state, advance production cursors, connect a scheduler, or call external/current-market tools.

## Selection objective

Candidate selection is lexicographic, not unrestricted PnL optimization:

1. all safety/no-lookahead/provenance constraints pass;
2. no incident/quarantine overlap;
3. candidate does not increase decision capability beyond TAKE/ABSTAIN;
4. dependence-aware incremental directional utility on CALIBRATION is non-worse than the uncalibrated agent under the fixed scoring rule;
5. coverage is reported and a trivial all-ABSTAIN candidate cannot be promoted;
6. fixed-cost normalized PAPER utility is secondary;
7. simulated PnL is descriptive only and cannot independently promote a candidate.

Coverage guard v1: `candidate_take_rate >= 0.5 * uncalibrated_take_rate` on CALIBRATION. If the uncalibrated take rate is zero, no calibration candidate is promotable.

## Evidence thresholds

TRAIN: 168 independent 1h clusters spanning at least 7 calendar days.

CALIBRATION: the next 168 independent 1h clusters spanning at least 7 additional calendar days.

This gives 336 independent clusters across at least 14 clean calendar days before a candidate can enter fixed-calibration PAPER. This is a screening threshold, not a strong evidence claim.

Strong HOLDOUT verdict: at least 600 independent 1h clusters spanning at least 25 calendar days after artifact freeze. Geometry check: 14 days can contain at most 336 independent hourly clusters; 600 requires at least 25 days.

Any candidate change creates a new `calibration_id` and resets HOLDOUT accumulation to zero.

## No-leakage rules

- TRAIN determines candidate cut points only.
- CALIBRATION selects one candidate.
- HOLDOUT never tunes that candidate.
- no row contributes to both candidate selection and its final holdout verdict;
- no random time shuffle;
- settlement is inaccessible until every compared arm has frozen its decision;
- model replacement creates new evaluation provenance and cannot reuse old agent performance as evidence.

## Recovery

Fail closed on:
- missing/corrupt calibration artifact;
- artifact hash/ID mismatch;
- epoch hash mismatch;
- code SHA mismatch;
- quarantine overlap;
- missing clean baseline;
- insufficient TRAIN/CALIBRATION evidence;
- cursor/provenance discontinuity;
- any current/future/outcome field in decision-time input.

Recovery returns to the last known fixed PAPER calibration artifact. It never silently re-fits.

## Rollback

Rollback is operational, not scientific rewriting:
- preserve the failed candidate and its evidence;
- mark status ROLLED_BACK;
- restore the previous fixed artifact by exact calibration_id;
- start a new evaluation epoch;
- do not merge failed-candidate samples into the prior holdout;
- do not erase the failed epoch.

A degraded holdout blocks promotion. It does not trigger automatic parameter search.
